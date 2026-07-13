"""횡보 범위 평균회귀 엔진 (Sideways v3) — 검증된 무상관 횡보 엔진.

박스권에서 공포의 과매도(-k σ, 하단밴드)를 지정가(메이커)로 받아, 평균회귀 후
탐욕의 과매수(상단밴드)에 돌려준다. 코어(추세)와 무상관(상관 +0.37)이라 합치면 분산↑.

진입(전부 충족 = 진짜 박스): ① 밴드폭 수축(변동성 짜부) ② 평균선 평탄(방향성 없음)
                          ③ MA200 위(하락장 칼받기 차단) + 하단밴드(-kσ) 지정가 매수
청산: 상단밴드(전체폭) 지정가 익절 / 진입가 -stop 깨짐(범위붕괴) 즉시 손절 / hold_bars 만료.

공유 풀: 최대 max_pos 동시보유, 각 budget/max_pos. 지정가 체결이 엣지의 전제(슬리피지 0).
검증(연속 15분봉 3년): CAGR+20% MDD-17%, 전·후반 양수, 10코인 전부 양수.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import pyupbit


def _fetch_15m(ticker: str, count: int) -> Optional[pd.DataFrame]:
    return pyupbit.get_ohlcv(ticker, interval="minute15", count=count)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SidewaysTrader:
    def __init__(self, cfg: dict, state_path: str = "data/sideways_paper.json", broker=None):
        sc = cfg.get("sideways", {})
        pcfg = cfg.get("portfolio", {})
        self.tickers: List[str] = list(sc.get("tickers", pcfg.get("tickers", ["KRW-BTC"])))
        self.budget = float(sc.get("budget_krw", 3_000_000))
        self.bb_n = int(sc.get("bb_n", 20))               # 평균/밴드 기간
        self.bb_k = float(sc.get("bb_k", 2.5))            # 밴드 폭(σ 배수) — 깊은 이탈만
        self.bbw_q = float(sc.get("bbw_q", 0.8))          # 밴드폭 < 평소(100봉)평균×이값 → 변동성수축
        self.slope_max = float(sc.get("slope_max", 0.02)) # 평균선 기울기 한계(평탄)
        self.ma200_buf = float(sc.get("ma200_buf", 0.95)) # 종가 > MA200×이값 (하락장 차단)
        self.stop_pct = float(sc.get("stop_pct", 0.05))   # 진입가 -이값 깨지면 손절
        self.vol_max = float(sc.get("vol_max", 1.2))      # 진입시 거래량 ≤ 평소×이값(시끄러운=박스깨짐 딥 차단)
        self.max_hold_bars = int(sc.get("max_hold_bars", 20))  # 최대 보유(15분봉 수)
        self.max_pos = int(sc.get("max_pos", 6))          # 동시보유(분산)
        self.interval_sec = float(sc.get("interval_sec", 300))
        self.fee = float(cfg.get("trade", {}).get("fee", 0.0005))
        self.state_path = state_path
        self.broker = broker
        self._halt = False

        self.cash = self.budget
        self.positions: List[dict] = []   # {ticker, state, entry, size, cost, upper, bars, day, uuid?}
        self.pending: List[dict] = []      # 미체결 지정가 매수 {ticker, uuid, target, krw, bar}
        self.realized_pnl = 0.0
        self.trades: List[dict] = []
        self.prices: Dict[str, float] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._stop = threading.Event()
        self._last_update: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_bar: Dict[str, str] = {}
        self._sell_requests: list = []        # 텔레그램 수동매도 요청(엔진 스레드가 처리)
        self._load()

    def set_halt(self, halted: bool):
        self._halt = bool(halted)

    def request_sell(self, ticker: str) -> bool:
        """외부(텔레그램) 매도 요청 적재. 엔진 스레드가 1초 내 시장가 청산(경쟁상태 방지)."""
        self._sell_requests.append(ticker)
        return True

    def _drain_sells(self):
        try:
            while self._sell_requests:
                tk = self._sell_requests.pop(0)
                for p in [x for x in self.positions if x["ticker"] == tk]:
                    self._sell(p, self.prices.get(tk, p["entry"]), "수동매도", maker=False)
            self._save()
        except Exception:
            self._last_error = traceback.format_exc(limit=3)

    # ---------- 상태 영속 ----------
    def _load(self):
        if os.path.exists(self.state_path):
            try:
                d = json.load(open(self.state_path, encoding="utf-8"))
                self.cash = d.get("cash", self.budget)
                self.positions = d.get("positions", [])
                self.pending = d.get("pending", [])
                self.realized_pnl = d.get("realized_pnl", 0.0)
                self.trades = d.get("trades", [])
                self._last_bar = d.get("last_bar", {})
            except Exception:
                pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            json.dump({"cash": self.cash, "positions": self.positions, "pending": self.pending,
                       "realized_pnl": self.realized_pnl, "trades": self.trades[-100:],
                       "last_bar": self._last_bar},
                      open(self.state_path + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            os.replace(self.state_path + ".tmp", self.state_path)
        except Exception:
            pass

    # ---------- 스레드 ----------
    def start_loop(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def enable(self): self._running.set()
    def disable(self): self._running.clear()
    def shutdown(self): self._stop.set()
    def is_enabled(self) -> bool: return self._running.is_set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._eval()
                self._last_update = _now(); self._last_error = None
                self._save()
            except Exception:
                self._last_error = traceback.format_exc(limit=3)
            for _ in range(int(self.interval_sec)):
                if self._stop.is_set(): break
                if self._sell_requests:
                    self._drain_sells()
                time.sleep(1)

    # ---------- 지표 ----------
    def _indi(self, df: pd.DataFrame) -> Optional[dict]:
        """최신 봉의 평균회귀 지표 묶음. 데이터 부족이면 None."""
        if df is None or len(df) < max(self.bb_n + 100, 200) + 1:
            return None
        c = df["close"]
        sma = c.rolling(self.bb_n).mean()
        std = c.rolling(self.bb_n).std()
        bbw = (2 * std) / sma
        bbw_ma = bbw.rolling(100).mean()
        slope = sma.diff(self.bb_n) / sma
        ma200 = c.rolling(200).mean()
        vol_ma = df["volume"].rolling(20).mean()
        i = -1
        if pd.isna(bbw_ma.iloc[i]) or pd.isna(ma200.iloc[i]) or pd.isna(std.iloc[i]) or pd.isna(vol_ma.iloc[i]):
            return None
        vol_ratio = float(df["volume"].iloc[i]) / float(vol_ma.iloc[i]) if float(vol_ma.iloc[i]) > 0 else 9.9
        lower = float(sma.iloc[i] - self.bb_k * std.iloc[i])
        upper = float(sma.iloc[i] + self.bb_k * std.iloc[i])
        in_range = (float(bbw.iloc[i]) < float(bbw_ma.iloc[i]) * self.bbw_q
                    and abs(float(slope.iloc[i])) < self.slope_max
                    and float(c.iloc[i]) > float(ma200.iloc[i]) * self.ma200_buf
                    and vol_ratio <= self.vol_max)          # 시끄러운 딥(박스깨짐) 차단
        return {"lower": lower, "upper": upper, "in_range": in_range,
                "low": float(df["low"].iloc[i]), "high": float(df["high"].iloc[i]),
                "close": float(c.iloc[i]), "bar": str(df.index[i])}

    # ---------- 평가 ----------
    def _eval(self):
        dfs: Dict[str, pd.DataFrame] = {}
        indi: Dict[str, dict] = {}
        for tk in self.tickers:
            df = _fetch_15m(tk, max(self.bb_n + 110, 260))
            if df is None:
                continue
            dfs[tk] = df
            d = self._indi(df)
            if d is not None:
                indi[tk] = d
                self.prices[tk] = float(pyupbit.get_current_price(tk) or d["close"])

        if not self._running.is_set():
            return

        # 0) 미체결 지정가 매수 처리(체결확인/취소)
        self._process_pending(indi)

        # 1) 청산: 상단밴드 익절 / 범위붕괴 손절 / 시간만료. (새 봉마다 보유봉 +1)
        for p in list(self.positions):
            tk = p["ticker"]; d = indi.get(tk)
            if d is None:
                continue
            new_bar = self._last_bar.get(tk) != d["bar"]
            if new_bar:
                p["bars"] = p.get("bars", 0) + 1
            px = self.prices.get(tk, d["close"])
            if d["high"] >= p["upper"]:                         # 상단밴드 도달 → 지정가 익절(전체폭)
                self._sell(p, p["upper"], "상단익절", maker=True)
            elif d["close"] <= p["entry"] * (1 - self.stop_pct):  # 범위붕괴 → 시장가 손절
                self._sell(p, px, "범위붕괴손절", maker=False)
            elif p.get("bars", 0) >= self.max_hold_bars:          # 시간만료 → 시장가 정리
                self._sell(p, px, "시간만료", maker=False)

        # 2) 진입: halt 아니고 슬롯 남으면, 진짜 박스 + 하단밴드 지정가 매수
        held = {p["ticker"] for p in self.positions} | {q["ticker"] for q in self.pending}
        for tk in self.tickers:
            if len(self.positions) + len(self.pending) >= self.max_pos or self.cash < 5000:
                break
            if self._halt or tk in held:
                continue
            d = indi.get(tk)
            if d is None:
                continue
            if self._last_bar.get(tk) == d["bar"]:   # 이 봉은 이미 평가
                continue
            if d["in_range"] and d["low"] <= d["lower"] and d["close"] > d["lower"] * (1 - self.stop_pct):
                self._place_buy(tk, d["lower"], d["upper"])
                held.add(tk)

        # 봉 마킹(코인별)
        for tk, d in indi.items():
            self._last_bar[tk] = d["bar"]

    # ---------- 주문 ----------
    def _place_buy(self, tk: str, lower: float, upper: float):
        """하단밴드 지정가 매수. 페이퍼면 즉시 체결(메이커가), 실거래면 지정가 주문→미체결 추적."""
        spend = min(self.budget / self.max_pos, self.cash)
        if spend < 5000 or lower <= 0:
            return
        if self.broker is None:                       # 페이퍼: 하단밴드에서 메이커 체결
            fee = spend * self.fee
            size = (spend - fee) / lower
            self.cash -= spend
            self.positions.append({"ticker": tk, "state": "open", "entry": lower, "size": size,
                                   "cost": spend, "buy_fee": fee,   # cost=총지출(매수수수료 포함)
                                   "upper": upper, "bars": 0, "day": _now()})
            self.trades.append({"time": _now(), "side": "buy", "ticker": tk, "price": lower, "amount": spend,
                                "fee": fee, "size": size})
        else:                                          # 실거래: 지정가 매수 주문(미체결 가능)
            uuid = self.broker.place_limit_buy(tk, lower, spend)
            if uuid:
                self.cash -= spend
                self.pending.append({"ticker": tk, "uuid": uuid, "target": lower, "upper": upper,
                                     "krw": spend, "bar": self._last_bar.get(tk, "")})

    def _process_pending(self, indi: Dict[str, dict]):
        """실거래 미체결 지정가 매수: 체결되면 포지션 전환, 박스 깨지면(범위이탈) 취소·환불."""
        if self.broker is None:
            return
        for q in list(self.pending):
            tk = q["ticker"]
            f = self.broker.check_order(q["uuid"])
            if f is not None and f.filled and f.volume > 0:        # 체결 완료
                self.positions.append({"ticker": tk, "state": "open", "entry": f.price, "size": f.volume,
                                       "cost": q["krw"], "buy_fee": f.fee,
                                       "upper": q["upper"], "bars": 0, "day": _now()})
                self.trades.append({"time": _now(), "side": "buy", "ticker": tk, "price": f.price, "amount": q["krw"],
                                    "fee": f.fee, "size": f.volume})
                self.pending.remove(q)
                continue
            # 미체결인데 박스가 깨졌거나 새 봉이 한참 지나면 취소(체결 안 되는 주문 정리)
            d = indi.get(tk)
            if d is not None and (not d["in_range"] or d["bar"] != q["bar"]):
                if self.broker.cancel_order(q["uuid"]):
                    self.cash += q["krw"]
                    self.pending.remove(q)

    def _sell(self, p: dict, price: float, reason: str, maker: bool):
        tk = p["ticker"]
        if self.broker is None:
            fee = price * p["size"] * self.fee
            gross = price * p["size"]
        elif maker:
            uuid = self.broker.place_limit_sell(tk, price, p["size"])
            if not uuid:
                return
            f = self.broker.check_order(uuid)
            if f is None or not f.filled:    # 아직 미체결 → 다음 평가에서 재시도(상단 도달 유지 시)
                self.broker.cancel_order(uuid)
                return
            price, gross, fee = f.price, f.krw, f.fee
        else:
            f = self.broker.execute_sell(tk, p["size"])
            if f is None:
                return
            price, gross, fee = f.price, f.krw, f.fee
        self.cash += gross - fee
        net = (gross - fee) - p["cost"]; self.realized_pnl += net   # cost=총지출이라 왕복수수료 반영
        fee_total = p.get("buy_fee", 0) + fee                        # 매수+매도 수수료 합
        self.trades.append({"time": _now(), "side": "sell", "ticker": tk, "price": price,
                            "amount": gross, "pnl": net, "reason": reason,
                            "fee": fee_total, "size": p["size"]})
        self.positions = [x for x in self.positions if x is not p]

    # ---------- 상태 ----------
    def status(self) -> dict:
        held = sum(p["size"] * self.prices.get(p["ticker"], p["entry"]) for p in self.positions)
        eq = self.cash + held
        return {
            "engine": "sideways", "running": self.is_enabled(),
            "total_equity": eq, "total_base": self.budget,
            "total_realized": self.realized_pnl,
            "total_pnl_pct": (eq / self.budget - 1) * 100 if self.budget else 0.0,
            "n_positions": len(self.positions), "n_pending": len(self.pending),
            "last_update": self._last_update, "error": self._last_error,
            "coins": [{"ticker": p["ticker"], "price": self.prices.get(p["ticker"]),
                       "holding": True, "avg": p["entry"], "amount": p["cost"],
                       "breakeven": p["cost"] / (p["size"] * (1 - self.fee)),
                       "unrealized": p["size"] * (self.prices.get(p["ticker"], p["entry"]) - p["entry"]),
                       "realized": 0.0, "bars": p.get("bars", 0), "target": p["upper"],
                       "recent_trades": []} for p in self.positions],
        }
