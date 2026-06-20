"""폭락 반등 엔진 (Capitulation) — 무상관 보너스 엔진.

거래량 폭발(진짜 패닉 항복) + 급락 직후 반등을 먹는다. 코어(추세)·새틀과 무상관(상관 0.01)이라
같은 자본 풀에 얹으면 위험 거의 안 늘고 수익↑(검증: 코어 +29.6→합체 +36.5%, MDD 17→18%).

진입(전부 충족): ① drop_days일 -drop_pct↓ 폭락 ② 거래량 > 20일평균×vol_mult(진짜 항복)
                ③ 시장 약세 아님(BTC 200일선+기울기, 차트게이트로 칼받기 회피)
청산: +tp_pct 익절(현재가, 장중도) 또는 hold_days 경과.
공유 풀: 최대 max_pos 동시보유, 각 budget/max_pos. 대부분 현금 대기(폭락은 드묾).

저빈도(일봉 신호) → 1시간 체크면 충분. 단타봇·코어와 자본/상태 분리(충돌 없음).
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


def _fetch_daily(ticker: str, count: int) -> Optional[pd.DataFrame]:
    return pyupbit.get_ohlcv(ticker, interval="day", count=count)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CapitulationTrader:
    def __init__(self, cfg: dict, state_path: str = "data/capitulation_paper.json", broker=None):
        cp = cfg.get("capitulation", {})
        pcfg = cfg.get("portfolio", {})
        self.tickers: List[str] = list(cp.get("tickers", pcfg.get("tickers", ["KRW-BTC"])))
        self.budget = float(cp.get("budget_krw", 3_000_000))     # 공유 풀 총액
        self.drop_pct = float(cp.get("drop_pct", 0.20))          # 폭락 기준
        self.drop_days = int(cp.get("drop_days", 5))
        self.vol_mult = float(cp.get("vol_mult", 2.0))           # 거래량 폭발 배수
        self.tp_pct = float(cp.get("tp_pct", 0.04))              # 익절
        self.hold_days = int(cp.get("hold_days", 5))             # 최대 보유
        self.max_pos = int(cp.get("max_pos", 2))                 # 동시보유
        self.gate_ticker = cp.get("gate_ticker", "KRW-BTC")
        self.ma_n = int(cp.get("gate_ma_n", 200))
        self.slope_n = int(cp.get("gate_slope_n", 20))
        self.interval_sec = float(cp.get("interval_sec", 3600))
        self.fee = float(cfg.get("trade", {}).get("fee", 0.0005))
        self.state_path = state_path
        self.broker = broker
        self._halt = False
        self._mkt_bear = False

        self.cash = self.budget
        self.positions: List[dict] = []     # {ticker, entry, size, cost, day, hold_left, price}
        self.realized_pnl = 0.0
        self.trades: List[dict] = []
        self.prices: Dict[str, float] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._stop = threading.Event()
        self._last_update: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_bar: Dict[str, str] = {}
        self._load()

    def set_halt(self, halted: bool):
        self._halt = bool(halted)

    # ---------- 상태 영속 ----------
    def _load(self):
        if os.path.exists(self.state_path):
            try:
                d = json.load(open(self.state_path, encoding="utf-8"))
                self.cash = d.get("cash", self.budget)
                self.positions = d.get("positions", [])
                self.realized_pnl = d.get("realized_pnl", 0.0)
                self.trades = d.get("trades", [])
                self._last_bar = d.get("last_bar", {})
            except Exception:
                pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            json.dump({"cash": self.cash, "positions": self.positions,
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
                time.sleep(1)

    # ---------- 평가 ----------
    def _update_gate(self, btc: pd.DataFrame):
        ma = btc["close"].rolling(self.ma_n).mean()
        if len(ma.dropna()) < self.slope_n + 1:
            return
        price = float(btc["close"].iloc[-1]); ma_now = float(ma.iloc[-1])
        slope = ma_now - float(ma.iloc[-1 - self.slope_n])
        self._mkt_bear = (price < ma_now) and (slope < 0)

    def _eval(self):
        # 일봉 수집(코인 + 게이트)
        need = max(self.ma_n + self.slope_n + 5, self.drop_days + 25)
        dfs = {}
        for tk in set(self.tickers + [self.gate_ticker]):
            df = _fetch_daily(tk, need)
            if df is not None and len(df) > self.drop_days + 21:
                dfs[tk] = df
        if self.gate_ticker in dfs:
            self._update_gate(dfs[self.gate_ticker])
        for tk in self.tickers:
            if tk in dfs:
                self.prices[tk] = float(pyupbit.get_current_price(tk) or dfs[tk]["close"].iloc[-1])

        if not self._running.is_set():
            return

        # 1) 청산: TP(현재가) 또는 보유만료
        for p in list(self.positions):
            px = self.prices.get(p["ticker"], p["entry"])
            new_day = self._last_bar.get("_date") != str(datetime.now().date())
            if px >= p["entry"] * (1 + self.tp_pct):
                self._sell(p, px, "익절")
            elif p["hold_left"] <= 0:
                self._sell(p, px, "보유만료")
        # 보유일 차감(하루 1회)
        today = str(datetime.now().date())
        if self._last_bar.get("_date") != today:
            for p in self.positions:
                p["hold_left"] -= 1
            self._last_bar["_date"] = today

        # 2) 진입: 약세 아니고 슬롯 남으면, 폭락+거래량 신호
        if self._halt or self._mkt_bear:
            return
        for tk in self.tickers:
            if len(self.positions) >= self.max_pos or self.cash < 5000:
                break
            if any(p["ticker"] == tk for p in self.positions):
                continue
            df = dfs.get(tk)
            if df is None:
                continue
            bar_id = str(df.index[-1].date())
            if self._last_bar.get(tk) == bar_id:        # 이 봉은 이미 평가
                continue
            ret = float(df["close"].iloc[-1] / df["close"].iloc[-1 - self.drop_days] - 1)
            vma = float(df["volume"].iloc[-21:-1].mean())
            vnow = float(df["volume"].iloc[-1])
            if ret <= -self.drop_pct and vnow > vma * self.vol_mult:
                self._last_bar[tk] = bar_id
                self._buy(tk, self.prices.get(tk, float(df["close"].iloc[-1])))

    def _buy(self, tk: str, price: float):
        spend = min(self.budget / self.max_pos, self.cash)
        if spend < 5000 or price <= 0:
            return
        if self.broker is None:
            fee = spend * self.fee
            size = (spend - fee) / price
        else:
            f = self.broker.execute_buy(tk, spend)
            if f is None: return
            price, size, fee, spend = f.price, f.volume, f.fee, f.krw
        self.cash -= spend
        self.positions.append({"ticker": tk, "entry": price, "size": size,
                               "cost": spend - fee, "day": _now(), "hold_left": self.hold_days})
        self.trades.append({"time": _now(), "side": "buy", "ticker": tk, "price": price, "amount": spend})

    def _sell(self, p: dict, price: float, reason: str):
        if self.broker is None:
            gross = price * p["size"]; fee = gross * self.fee
        else:
            f = self.broker.execute_sell(p["ticker"], p["size"])
            if f is None: return
            price, gross, fee = f.price, f.krw, f.fee
        self.cash += gross - fee
        net = (gross - fee) - p["cost"]; self.realized_pnl += net
        self.trades.append({"time": _now(), "side": "sell", "ticker": p["ticker"], "price": price,
                            "amount": gross, "pnl": net, "reason": reason})
        self.positions = [x for x in self.positions if x is not p]

    # ---------- 상태 ----------
    def status(self) -> dict:
        held = sum(p["size"] * self.prices.get(p["ticker"], p["entry"]) for p in self.positions)
        eq = self.cash + held
        return {
            "engine": "capitulation", "running": self.is_enabled(),
            "total_equity": eq, "total_base": self.budget,
            "total_realized": self.realized_pnl,
            "total_pnl_pct": (eq / self.budget - 1) * 100 if self.budget else 0.0,
            "mkt_bear": self._mkt_bear, "n_positions": len(self.positions),
            "last_update": self._last_update, "error": self._last_error,
            "coins": [{"ticker": p["ticker"], "price": self.prices.get(p["ticker"]),
                       "holding": True, "avg": p["entry"],
                       "unrealized": p["size"] * (self.prices.get(p["ticker"], p["entry"]) - p["entry"]),
                       "realized": 0.0, "hold_left": p["hold_left"],
                       "recent_trades": []} for p in self.positions],
        }
