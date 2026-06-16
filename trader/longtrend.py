"""중장기 추세추종 엔진 (라이브) — 일봉 돈키언 터틀식. 단타봇과 별도 자본 풀로 병행.

검증(position_daily, 일봉 3년): 평균 +76% / 낙폭 33% (그냥보유 +8%/72%). 하락장 자동회피.
  진입: 종가 > 200일선 AND 종가 ≥ 최근 entry_n일 고점(돌파)
  청산: 종가 ≤ 최근 exit_n일 저점 OR 고점 − ATR×k(샹들리에)
저빈도(코인당 연 몇 회) → 하루 1번 체크면 충분. 단타봇과 자본/상태 완전 분리(충돌 없음).
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


def fetch_daily(ticker: str, count: int) -> Optional[pd.DataFrame]:
    """일봉 count개(테스트 시 monkeypatch). KST 인덱스."""
    return pyupbit.get_ohlcv(ticker, interval="day", count=count)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LongTrendTrader:
    def __init__(self, cfg: dict, state_path: str = "data/longterm_paper.json",
                 broker=None):
        lt = cfg.get("longterm", {})
        pcfg = cfg.get("portfolio", {})
        self.tickers: List[str] = list(lt.get("tickers", pcfg.get("tickers", ["KRW-BTC"])))
        self.per_coin = float(lt.get("per_coin_krw", 1_200_000))
        self.per_coin_overrides = lt.get("per_coin_overrides", {}) or {}   # 종목별 차등 예산(메이저↑)
        self.entry_n = int(lt.get("entry_n", 50))      # 돌파 고점 구간(일)
        self.exit_n = int(lt.get("exit_n", 20))        # 청산 저점 구간(일)
        self.atr_k = float(lt.get("atr_k", 3.0))       # 샹들리에 ATR 배수
        self.ma_n = int(lt.get("ma_n", 200))           # 장기추세 필터(일)
        self.interval_sec = float(lt.get("interval_sec", 3600))   # 체크 주기(일봉이라 길게)
        self.fee = float(cfg.get("trade", {}).get("fee", 0.0005))
        self.unit = int(lt.get("unit", 1))             # 1=일봉
        self.state_path = state_path
        self.broker = broker        # None=모의 회계 / LedgerBroker=단일계정 실주문(자기 장부 기준)
        self._halt = False          # 차단기 작동 시 True → 신규 진입만 차단(청산은 계속)

        self.coins: Dict[str, dict] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_update: Optional[str] = None
        self._last_error: Optional[str] = None
        self._load()

    def _per_coin(self, tk: str) -> float:
        return float(self.per_coin_overrides.get(tk, self.per_coin))

    def _fresh(self, tk: str) -> dict:
        return {"cash": self._per_coin(tk), "realized_pnl": 0.0, "position": None,
                "trades": [], "last_date": None, "price": None}

    def _load(self):
        data = {}
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        for tk in self.tickers:
            self.coins[tk] = {**self._fresh(tk), **data.get(tk, {})}

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        dump = {tk: {"cash": c["cash"], "realized_pnl": c["realized_pnl"],
                     "position": c["position"], "trades": c["trades"][-50:],
                     "last_date": c["last_date"]} for tk, c in self.coins.items()}
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

    def _run(self):
        while not self._stop.is_set():
            try:
                for tk in self.tickers:
                    self._eval_coin(tk)
                self._last_update = _now()
                self._last_error = None
                self._save()
            except Exception:
                self._last_error = traceback.format_exc(limit=3)
            for _ in range(int(self.interval_sec)):
                if self._stop.is_set():
                    break
                time.sleep(1)

    # ---------- 종목 평가(일봉) ----------
    def _eval_coin(self, tk: str):
        c = self.coins[tk]
        df = fetch_daily(tk, self.ma_n + 80)
        if df is None or len(df) < self.ma_n + 5:
            return
        ma = df["close"].rolling(self.ma_n).mean()
        dhi = df["high"].rolling(self.entry_n).max().shift(1)   # 직전까지 고점(미래참조 방지)
        dlo = df["low"].rolling(self.exit_n).min().shift(1)
        atr = _atr(df)
        i = len(df) - 1
        price = float(df["close"].iloc[i])
        c["price"] = price
        date = str(df.index[i].date())

        if not self._running.is_set():
            return

        pos = c["position"]
        if pos is not None:
            pos["peak"] = max(pos["peak"], float(df["high"].iloc[i]))
            chand = pos["peak"] - self.atr_k * float(atr.iloc[i])
            if price <= float(dlo.iloc[i]) or price <= chand:
                self._sell(tk, price, "추세붕괴" if price <= float(dlo.iloc[i]) else "샹들리에")
            return

        # 미보유: 상승추세(>200일선) + 돈키언 고점 돌파 → 진입(하루 1회)
        if c["last_date"] == date:
            return
        c["last_date"] = date
        if price > float(ma.iloc[i]) and price >= float(dhi.iloc[i]):
            self._buy(tk, price)

    def set_halt(self, halted: bool):
        self._halt = bool(halted)

    def _buy(self, tk: str, price: float):
        if self._halt:               # 차단기: 신규 진입 차단
            return
        c = self.coins[tk]
        spend = c["cash"]
        if spend < 5000 or price <= 0:
            return
        # 모의=즉시체결 가정 / 라이브=실주문 후 실체결로 회계(자기 서브예산 spend만 사용)
        if self.broker is None:
            fee = spend * self.fee
            size = (spend - fee) / price
        else:
            f = self.broker.execute_buy(tk, spend)
            if f is None:
                return
            price, size, fee, spend = f.price, f.volume, f.fee, f.krw
        c["cash"] -= spend
        c["position"] = {"entry": price, "size": size, "cost": spend, "peak": price, "time": _now()}
        c["trades"].append({"time": _now(), "side": "buy", "price": price, "amount": spend})

    def _sell(self, tk: str, price: float, reason: str):
        c = self.coins[tk]
        p = c["position"]
        if p is None:
            return
        # 자기가 산 수량(p["size"])만 매도 → 상대 엔진 코인 불가침
        if self.broker is None:
            gross = price * p["size"]
            fee = gross * self.fee
        else:
            f = self.broker.execute_sell(tk, p["size"])
            if f is None:
                return
            price, gross, fee = f.price, f.krw, f.fee
        c["cash"] += gross - fee
        net = (gross - fee) - p["cost"]
        c["realized_pnl"] += net
        c["trades"].append({"time": _now(), "side": "sell", "price": price,
                            "amount": gross, "pnl": net, "reason": reason})
        c["position"] = None

    # ---------- 상태 ----------
    def status(self) -> dict:
        coins = []
        tot_eq = tot_base = 0.0
        for tk in self.tickers:
            c = self.coins[tk]
            pos = c["position"]
            held = pos["size"] * (c["price"] or pos["entry"]) if pos else 0.0
            eq = c["cash"] + held
            tot_eq += eq
            tot_base += self._per_coin(tk)
            coins.append({
                "ticker": tk, "price": c["price"], "holding": pos is not None,
                "avg": pos["entry"] if pos else None,
                "pnl_pct": ((c["price"] / pos["entry"] - 1) * 100) if pos and c["price"] else None,
                "realized": c["realized_pnl"], "equity": eq,
                "unrealized": (pos["size"] * (c["price"] - pos["entry"])) if (pos and c["price"]) else 0.0,
                "recent_trades": list(reversed(c["trades"][-5:])),
            })
        return {
            "engine": "longterm", "running": self.is_enabled(),
            "total_equity": tot_eq, "total_base": tot_base,
            "total_realized": sum(c["realized_pnl"] for c in self.coins.values()),
            "total_pnl_pct": (tot_eq / tot_base - 1) * 100 if tot_base else 0.0,
            "last_update": self._last_update, "error": self._last_error,
            "coins": coins,
        }
