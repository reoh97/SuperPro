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
import copy
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


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LiveTrader:
    def __init__(self, cfg: dict, advisor: Optional[AIAdvisor] = None,
                 news: Optional[NewsFeed] = None, state_path: str = "data/live_paper.json"):
        self.cfg = cfg
        self.advisor = advisor
        self.news = news
        self.state_path = state_path

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
        self.exit_on_down = bool(scfg.get("exit_on_down", True))   # DOWN전환 시 보유분 청산(V1)
        ucfg = scfg.get("uptrend", {})
        self.pyramid = bool(ucfg.get("pyramid", False))            # 상승장 불타기
        self.pyramid_step = float(ucfg.get("pyramid_step", 0.04))
        self.market_ticker = cfg.get("ticker", "KRW-BTC")  # AI 장세판단 대표코인

        # 상승장 공격모드(3단): AI 확신도에 따라 강함/적당/그외. BULL일 때만, 그 외 장세는 보수.
        #   강함(≥strong): adx완화+trail넓게+DOWN홀드.  적당(min~strong): 선별진입(adx)+trail중간+DOWN청산.
        #   근거(BTC/USD 프록시): 불장 적용 시 보수대비 큰 개선. 약세/횡보엔 절대 적용 안 됨→AI게이트로 분기.
        bm = scfg.get("bull_mode", {})
        self.bull_enabled = bool(bm.get("enabled", True))
        self.bull_min_conf = float(bm.get("min_confidence", 60))
        self.bull_strong_conf = float(bm.get("strong_confidence", 75))
        self.bull_hold_down = bool(bm.get("hold_through_down", True))           # 강함 티어
        self.bull_pyramid = bool(bm.get("pyramid", True))                       # 강함 티어: 보루를 승자에 추가(불타기)
        self.bull_pyramid_step = float(bm.get("pyramid_step", 0.04))            #   직전 진입가 +step 추가상승마다 1트랜치
        self._cfg_bull = copy.deepcopy(cfg)
        ub = self._cfg_bull.setdefault("scalp", {}).setdefault("uptrend", {})
        ub["require_adx_rising"] = bool(bm.get("require_adx_rising", False))
        ub["trail_pct"] = float(bm.get("trail_pct", 0.04))
        mod = bm.get("moderate", {})                                            # 적당 티어
        self.bull_mod_hold_down = bool(mod.get("hold_through_down", False))
        self._cfg_bull_mod = copy.deepcopy(cfg)
        um = self._cfg_bull_mod.setdefault("scalp", {}).setdefault("uptrend", {})
        um["require_adx_rising"] = bool(mod.get("require_adx_rising", True))
        um["trail_pct"] = float(mod.get("trail_pct", 0.03))

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

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        dump = {}
        for tk, c in self.coins.items():
            dump[tk] = {"cash": c["cash"], "realized_pnl": c["realized_pnl"],
                        "position": c["position"], "trades": c["trades"][-100:],
                        "last_bar": c["last_bar"], "confirmed": c["confirmed"],
                        "streak_reg": c["streak_reg"], "streak": c["streak"]}
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

    def _bull_tier(self) -> Optional[str]:
        """AI=BULL 확신도에 따라 'strong'(강함)/'moderate'(적당)/None(보수). 그 외 장세=None."""
        if not self.bull_enabled:
            return None
        v = self._ai_view
        if v is None or v.regime != "BULL" or v.risk_off:
            return None
        conf = getattr(v, "confidence", None)
        if conf is None:
            return "strong"                       # 확신도 없으면 BULL=강함으로 간주
        if conf < self.bull_min_conf:
            return None                           # 확신 약하면 보수
        return "strong" if conf >= self.bull_strong_conf else "moderate"

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
        tier = self._bull_tier()                          # 'strong' | 'moderate' | None
        if tier == "strong":
            cfg_eval, hold_down = self._cfg_bull, self.bull_hold_down
        elif tier == "moderate":
            cfg_eval, hold_down = self._cfg_bull_mod, self.bull_mod_hold_down
        else:
            cfg_eval, hold_down = self.cfg, False
        sig = strategies.evaluate(prev, row, confirmed, cfg_eval, self.fee)

        # ----- 보유 중: 청산 감시(매 루프, 현재가 기준) -----
        if pos is not None:
            if new_bar:
                pos["bars_held"] += 1
            avg = pos["cost"] / pos["size"]

            # DOWN 확정전환 → 즉시 탈출 (단 홀드 티어에선 눌림목으로 보고 안 팖)
            exit_down = self.exit_on_down and not hold_down
            if exit_down and confirmed == regime.DOWN and pos["regime"] != regime.DOWN:
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
                # 불타기(trail): 강한 상승(strong) 티어에서만 보루를 승자에 추가. 직전가 +step 상승 + UP 지속.
                else:
                    pyr_on = self.bull_pyramid if tier == "strong" else self.pyramid
                    pyr_step = self.bull_pyramid_step if tier == "strong" else self.pyramid_step
                    if (mode == "trail" and pyr_on and confirmed == regime.UP
                            and price >= pos["last_entry"] * (1 + pyr_step)):
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
        tier = self._bull_tier()
        gate = ("현금보존(신규 중단)" if not allowed
                else "🔥 상승장 공격모드(강함: 추세추종 강화)" if tier == "strong"
                else "⚡ 상승장 중도모드(적당: 선별진입+추세)" if tier == "moderate"
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
            "ai_regime": {
                "enabled": bool(self.advisor and self.advisor.enabled),
                "regime": v.regime if v else None,
                "risk_off": v.risk_off if v else None,
                "confidence": v.confidence if v else None,
                "reason": v.reason if v else None,
                "decided_at": self._ai_at,
                "disabled_reason": self.advisor.disabled_reason if self.advisor else None,
                "bull_tier": tier,                 # 'strong' | 'moderate' | None
            },
            "coins": coins,
        }
