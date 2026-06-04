"""멀티코인 라이브 트레이딩 엔진 (모의 우선).

백테스트로 검증한 로직을 실시간으로 이식:
  - 종목별 독립 예산(per_coin_krw)·포지션·현금
  - 국면별 전략(상승=트레일링, 횡보=박스 빠른익절, 하락=관망/단타) + 분할매수
  - DOWN 확정전환 시 즉시 탈출
  - **AI 시장국면 게이트**: AI가 BEAR/risk_off로 보면 신규매수 중단(현금 보존)

백테스트와의 차이(라이브 특성):
  - 닫힌 봉(직전 15m봉)으로 신호/지표/국면을 판단하고, 청산은 '현재가'로 매 루프 감시
  - 진입/추가/보유봉수 갱신은 '새 봉이 닫혔을 때' 1회만 (같은 봉 중복매매 방지)

주의: 기본 paper(모의). live(실거래)는 향후 종목별 주문 연결 필요(현재 미구현).
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

import pyupbit

from . import backtest, indicators, regime, strategies
from .ai_advisor import AIAdvisor, RegimeView
from .market import build_market_summary
from .news import NewsFeed
from .notify import TelegramNotifier


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LiveTrader:
    def __init__(self, cfg: dict, advisor: Optional[AIAdvisor] = None,
                 news: Optional[NewsFeed] = None, state_path: str = "data/live_paper.json",
                 notifier: Optional[TelegramNotifier] = None):
        self.cfg = cfg
        self.advisor = advisor
        self.news = news
        self.state_path = state_path
        self.notifier = notifier

        pcfg = cfg.get("portfolio", {})
        self.tickers: List[str] = list(pcfg.get("tickers", [cfg.get("ticker", "KRW-BTC")]))
        self.per_coin = float(pcfg.get("per_coin_krw", 2000000))
        self.n_tranche = int(pcfg.get("tranches", 4))
        self.add_drop = float(pcfg.get("add_drop_pct", 0.02))
        self.hard_sl = float(pcfg.get("hard_sl_pct", 0.06))
        self.max_hold = int(pcfg.get("max_hold_bars", 480))
        self.fee = float(cfg["trade"]["fee"])
        self.unit = int(cfg.get("timeframe_min", 15))
        scfg = cfg.get("scalp", {})
        self.confirm_bars = int(scfg.get("confirm_bars", 3))
        ucfg = scfg.get("uptrend", {})
        self.pyramid = bool(ucfg.get("pyramid", False))            # 상승장 불타기
        self.pyramid_step = float(ucfg.get("pyramid_step", 0.04))
        self.market_ticker = cfg.get("ticker", "KRW-BTC")  # AI 장세판단 대표코인

        # 종목별 상태: {ticker: {...}}
        self.coins: Dict[str, dict] = {}

        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None
        self._last_update: Optional[str] = None

        # AI 시장국면
        self._ai_view: Optional[RegimeView] = None
        self._ai_at: Optional[str] = None
        self._last_ai_ts: float = 0.0

        # 목표수익 서킷브레이커
        ccfg = cfg.get("circuit", {})
        self.circuit_enabled = bool(ccfg.get("enabled", True))
        self.target_pct = float(ccfg.get("profit_target_pct", 1.1))
        self.total_budget = self.per_coin * len(self.tickers)
        self._equity_base: float = self.total_budget   # 수익률 측정 기준선(재개 시 리셋)
        self._target_hit: bool = False
        self._target_hit_at: Optional[str] = None

        self._load()

    # ---------- 상태 영속화 ----------
    def _fresh_coin(self) -> dict:
        return {"cash": self.per_coin, "realized_pnl": 0.0, "position": None,
                "trades": [], "last_bar": None, "confirmed": regime.SIDEWAYS,
                "streak_reg": None, "streak": 0, "price": None, "reg_raw": None}

    def _load(self):
        data = {}
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        for tk in self.tickers:
            self.coins[tk] = {**self._fresh_coin(), **data.get(tk, {})}
        meta = data.get("__meta__", {}) if isinstance(data, dict) else {}
        self._equity_base = float(meta.get("equity_base", self.total_budget))
        self._target_hit = bool(meta.get("target_hit", False))
        self._target_hit_at = meta.get("target_hit_at")

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        dump = {}
        for tk, c in self.coins.items():
            dump[tk] = {"cash": c["cash"], "realized_pnl": c["realized_pnl"],
                        "position": c["position"], "trades": c["trades"][-100:],
                        "last_bar": c["last_bar"], "confirmed": c["confirmed"],
                        "streak_reg": c["streak_reg"], "streak": c["streak"]}
        dump["__meta__"] = {"equity_base": self._equity_base,
                            "target_hit": self._target_hit,
                            "target_hit_at": self._target_hit_at}
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    # ---------- 스레드 제어 ----------
    def start_loop(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def enable(self):
        self._running.set()

    def disable(self):
        self._running.clear()

    def shutdown(self):
        self._stop.set()

    def is_enabled(self) -> bool:
        return self._running.is_set()

    # ---------- 메인 루프 ----------
    def _run(self):
        interval = max(float(self.cfg["loop"]["interval_sec"]), 5)
        while not self._stop.is_set():
            try:
                self._refresh_ai_regime()
                for tk in self.tickers:
                    self._eval_coin(tk)
                self._check_circuit()        # 목표수익 도달 시 전량청산+정지+알림
                self._last_update = _now()
                self._last_error = None
                self._save()
            except Exception:
                self._last_error = traceback.format_exc(limit=3)
            for _ in range(int(interval)):
                if self._stop.is_set():
                    break
                time.sleep(1)

    # ---------- AI 시장국면 ----------
    def _refresh_ai_regime(self):
        if self.advisor is None or not self.advisor.enabled:
            return
        ai_cfg = self.cfg.get("ai", {})
        cooldown = float(ai_cfg.get("regime_interval_sec", ai_cfg.get("min_interval_sec", 900)))
        now = time.time()
        if self._ai_view is not None and (now - self._last_ai_ts) < cooldown:
            return
        df = backtest.fetch_minutes(self.market_ticker, self.unit, 200)
        if df is None or len(df) < 60:
            return
        summary = build_market_summary(df, self.cfg)
        headlines = self.news.latest() if self.news else []
        view = self.advisor.decide_regime(summary, headlines)
        with self._lock:
            self._ai_view = view
            self._ai_at = _now()
        self._last_ai_ts = now

    def _entry_policy(self):
        """AI 장세 게이트(차등). 반환: (allowed, allowed_regimes, size_mult).
        - risk_off            → 완전 현금(진입 0)
        - BEAR + bear_defensive → **UP(추세추종) 차단**, 박스(SIDEWAYS)+과매도반등(DOWN)만.
            검증: 약세장 UP 차단 시 일평균 -0.003%→+0.015%(박스가 유일한 수익원, UP가 손실원)
        - BEAR + 방어off       → 진입 0
        - 그 외/AI없음/오류     → 전체 허용(fail-open)
        """
        ALL = {regime.UP, regime.SIDEWAYS, regime.DOWN}
        v = self._ai_view
        if v is None or v.regime == "ERROR":
            return True, ALL, 1.0
        if v.risk_off:
            return False, set(), 1.0
        if v.regime == "BEAR":
            ai = self.cfg.get("ai", {})
            if not bool(ai.get("bear_defensive", True)):
                return False, set(), 1.0
            return True, {regime.SIDEWAYS, regime.DOWN}, float(ai.get("bear_size_mult", 1.0))
        return True, ALL, 1.0

    def _entries_allowed(self) -> bool:
        return self._entry_policy()[0]

    # ---------- 목표수익 서킷브레이커 ----------
    def _totals(self):
        """현재 총평가액·실현손익과, 기준선 대비 수익률(%)을 계산."""
        equity = realized = 0.0
        for tk in self.tickers:
            c = self.coins[tk]
            price = c.get("price")
            p = c["position"]
            coin_val = (price or 0.0) * (p["size"] if p else 0.0)
            equity += c["cash"] + coin_val
            realized += c["realized_pnl"]
        base = self._equity_base or self.total_budget
        ret = (equity - base) / base * 100 if base else 0.0
        return equity, realized, ret

    def _check_circuit(self):
        """기준선 대비 수익률이 목표(예: +1.1%)에 도달하면:
        보유 전량을 현재가로 청산 → 매매 완전정지 → 텔레그램 알림.
        재개(resume)는 대시보드 [재개] 버튼에서. 재개 시 기준선이 리셋됨."""
        if not self.circuit_enabled or self._target_hit or not self._running.is_set():
            return
        equity, _realized, ret = self._totals()
        if ret < self.target_pct:
            return
        # 전량 청산(현재가). DOWN 즉시탈출과 동일하게 _sell 사용.
        for tk in self.tickers:
            c = self.coins[tk]
            if c["position"] is not None and c.get("price"):
                self._sell(tk, c["price"], "target")
        self._target_hit = True
        self._target_hit_at = _now()
        self.disable()
        final_equity, final_realized, final_ret = self._totals()
        self._notify_target(final_equity, final_realized, final_ret)

    def _notify_target(self, equity: float, realized: float, ret: float):
        if self.notifier is None or not self.notifier.enabled:
            return
        mode = "실거래" if self.cfg.get("mode") == "live" else "모의"
        msg = (
            f"🎯 <b>목표 수익 도달 — 매매 정지</b> ({mode})\n"
            f"수익률: <b>+{ret:.2f}%</b> (목표 +{self.target_pct:g}%)\n"
            f"총 평가액: {equity:,.0f}원\n"
            f"실현 손익: {realized:+,.0f}원\n"
            f"기준선: {self._equity_base:,.0f}원\n"
            f"시각: {self._target_hit_at}\n\n"
            f"보유 전량 청산 후 신규매매를 멈췄습니다.\n"
            f"계속하려면 대시보드에서 [재개]를 눌러주세요 "
            f"(기준선이 현재 평가액으로 리셋되어 거기서 다시 +{self.target_pct:g}% 목표)."
        )
        self.notifier.send(msg)

    def resume(self):
        """목표 도달 정지 상태를 해제하고 매매 재개. 기준선을 현재 평가액으로 리셋."""
        equity, _realized, _ret = self._totals()
        self._equity_base = equity if equity > 0 else self.total_budget
        self._target_hit = False
        self._target_hit_at = None
        self.enable()
        self._save()

    # ---------- 종목 평가 ----------
    def _eval_coin(self, tk: str):
        c = self.coins[tk]
        df = backtest.fetch_minutes(tk, self.unit, 200)
        price = pyupbit.get_current_price(tk)
        if df is None or len(df) < 60 or not price:
            return
        d = indicators.enrich(df, self.cfg)
        if len(d) < 3:
            return
        row, prev = d.iloc[-2], d.iloc[-3]      # 직전 '닫힌' 봉으로 신호 판단
        bar_id = str(d.index[-2])
        c["price"] = float(price)

        new_bar = (c["last_bar"] != bar_id)
        if new_bar:
            # 확정 국면 갱신(휩쏘 방지)
            raw = regime.classify(row, self.cfg)
            c["reg_raw"] = raw
            if raw == c["streak_reg"]:
                c["streak"] += 1
            else:
                c["streak_reg"], c["streak"] = raw, 1
            if c["streak"] >= self.confirm_bars:
                c["confirmed"] = raw
            c["last_bar"] = bar_id

        # 매매 비활성(정지) 시: 시세·국면만 갱신하고 주문은 내지 않음
        if not self._running.is_set():
            return

        confirmed = c["confirmed"]
        pos = c["position"]
        sig = strategies.evaluate(prev, row, confirmed, self.cfg, self.fee)

        # ----- 보유 중: 청산 감시(매 루프, 현재가 기준) -----
        if pos is not None:
            if new_bar:
                pos["bars_held"] += 1
            avg = pos["cost"] / pos["size"]

            # DOWN 확정전환 → 즉시 탈출
            if confirmed == regime.DOWN and pos["regime"] != regime.DOWN:
                self._sell(tk, price, "regime"); return

            mode = pos["exit_mode"]
            if mode == "trail":
                pos["peak"] = max(pos["peak"], float(price))
                stop = max(pos["sl_price"], pos["peak"] * (1 - pos["trail_pct"]))
                if price <= stop:
                    self._sell(tk, price, "trail"); return
            elif mode == "scalp":
                if price <= pos["sl_price"]:
                    self._sell(tk, price, "sl"); return
                if price >= avg * (1 + pos["tp_pct"]):
                    self._sell(tk, price, "tp"); return
                if pos["bars_held"] >= self.max_hold:
                    self._sell(tk, price, "timeout"); return
            else:  # fixed (분할매수)
                if pos["used"] >= self.n_tranche and price <= avg * (1 - self.hard_sl):
                    self._sell(tk, price, "sl"); return
                if price >= avg * (1 + pos["tp_pct"]):
                    self._sell(tk, price, "tp"); return
                if pos["bars_held"] >= self.max_hold:
                    self._sell(tk, price, "timeout"); return

            # 추가(새 봉에서만). 현재 국면이 AI 정책상 허용될 때만(BEAR선 UP 차단).
            allowed, regs, mult = self._entry_policy()
            if new_bar and allowed and confirmed in regs and pos["used"] < self.n_tranche:
                # 분할매수(fixed): 직전가 -add_drop 하락 + 신호
                if (mode == "fixed" and sig.should_enter
                        and price <= pos["last_entry"] * (1 - self.add_drop)):
                    self._buy(tk, price, sig, add=True, size_mult=mult)
                # 불타기(trail): 직전가 +pyramid_step 상승 + UP 지속
                elif (mode == "trail" and self.pyramid and confirmed == regime.UP
                        and price >= pos["last_entry"] * (1 + self.pyramid_step)):
                    self._buy(tk, price, sig, add=True, size_mult=mult)
            return

        # ----- 미보유: 새 봉 + 신호 + AI 정책상 현재 국면 허용 시 진입 -----
        if new_bar and sig.should_enter:
            allowed, regs, mult = self._entry_policy()
            if allowed and confirmed in regs:
                self._buy(tk, price, sig, add=False, size_mult=mult)

    # ---------- 체결(모의) ----------
    def _tranche_krw(self) -> float:
        return self.per_coin / self.n_tranche

    def _buy(self, tk: str, price: float, sig, add: bool, size_mult: float = 1.0):
        c = self.coins[tk]
        spend = min(self._tranche_krw() * size_mult, c["cash"])
        if spend <= 0 or price <= 0:
            return
        fee = spend * self.fee
        vol = (spend - fee) / price
        c["cash"] -= spend
        if add and c["position"] is not None:
            p = c["position"]
            p["size"] += vol
            p["cost"] += spend - fee
            p["fees"] += fee
            p["used"] += 1
            p["last_entry"] = price
        else:
            c["position"] = {
                "size": vol, "cost": spend - fee, "fees": fee, "used": 1,
                "last_entry": price, "entry_time": _now(), "bars_held": 0,
                "regime": c["confirmed"], "exit_mode": sig.exit_mode,
                "tp_pct": sig.tp_pct, "trail_pct": sig.trail_pct,
                "sl_price": price * (1 - sig.sl_pct), "peak": price,
            }
        c["trades"].append({"time": _now(), "side": "buy", "price": price,
                            "volume": vol, "amount": spend, "fee": fee,
                            "reason": sig.reason})

    def _sell(self, tk: str, price: float, reason: str):
        c = self.coins[tk]
        p = c["position"]
        if p is None:
            return
        vol = p["size"]
        gross = price * vol
        fee = gross * self.fee
        c["cash"] += gross - fee
        net = (gross - fee) - (p["cost"] + p["fees"])
        c["realized_pnl"] += net
        c["trades"].append({"time": _now(), "side": "sell", "price": price,
                            "volume": vol, "amount": gross, "fee": fee,
                            "pnl": net, "reason": reason})
        c["position"] = None

    # ---------- 대시보드용 스냅샷 ----------
    def snapshot(self) -> Dict[str, Any]:
        coins = []
        total_equity = total_realized = total_budget = 0.0
        for tk in self.tickers:
            c = self.coins[tk]
            price = c.get("price")
            p = c["position"]
            coin_val = (price or 0.0) * (p["size"] if p else 0.0)
            equity = c["cash"] + coin_val
            avg = (p["cost"] / p["size"]) if p else 0.0
            unrl = ((price - avg) * p["size"]) if (p and price) else 0.0
            total_equity += equity
            total_realized += c["realized_pnl"]
            total_budget += self.per_coin
            coins.append({
                "ticker": tk, "price": price, "cash": c["cash"],
                "confirmed": c["confirmed"], "reg_raw": c.get("reg_raw"),
                "has_position": p is not None,
                "avg_price": avg, "volume": p["size"] if p else 0.0,
                "tranches_used": p["used"] if p else 0,
                "exit_mode": p["exit_mode"] if p else None,
                "coin_value": coin_val, "equity": equity,
                "unrealized": unrl, "realized": c["realized_pnl"],
                "recent_trades": list(reversed(c["trades"][-5:])),
            })
        v = self._ai_view
        allowed, regs, mult = self._entry_policy()
        gate = ("현금보존(신규 중단)" if not allowed
                else f"약세장 방어(박스+반등, UP차단 ×{mult:g})" if regime.UP not in regs
                else "정상 진입")
        return {
            "running": self._running.is_set(),
            "mode": self.cfg.get("mode", "paper"),
            "last_update": self._last_update,
            "last_error": self._last_error,
            "entries_allowed": allowed,
            "gate": gate,
            "total": {"budget": total_budget, "equity": total_equity,
                      "realized": total_realized,
                      "pnl": total_equity - total_budget,
                      "ret_pct": (total_equity - total_budget) / total_budget * 100 if total_budget else 0.0},
            "circuit": {
                "enabled": self.circuit_enabled,
                "target_pct": self.target_pct,
                "target_hit": self._target_hit,
                "hit_at": self._target_hit_at,
                "base": self._equity_base,
                "ret_pct": ((total_equity - self._equity_base) / self._equity_base * 100
                            if self._equity_base else 0.0),
                "telegram": bool(self.notifier and self.notifier.enabled),
                "telegram_reason": (self.notifier.disabled_reason if self.notifier else "미설정"),
            },
            "ai_regime": {
                "enabled": bool(self.advisor and self.advisor.enabled),
                "regime": v.regime if v else None,
                "risk_off": v.risk_off if v else None,
                "confidence": v.confidence if v else None,
                "reason": v.reason if v else None,
                "decided_at": self._ai_at,
                "disabled_reason": self.advisor.disabled_reason if self.advisor else None,
            },
            "coins": coins,
        }
