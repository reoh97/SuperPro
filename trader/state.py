"""포트폴리오 상태(잔고/포지션/거래내역)와 JSON 영속화.

모의거래의 가상 잔고를 보관하고, 거래 로그를 파일로 남겨
프로그램을 재시작해도 이어서 볼 수 있게 합니다.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Trade:
    time: str
    side: str          # "buy" | "sell"
    price: float
    volume: float      # 코인 수량
    amount: float      # 체결 금액(원, 수수료 제외 총액)
    fee: float
    pnl: Optional[float] = None   # 매도 시 실현 손익(원)


@dataclass
class Portfolio:
    krw: float = 0.0            # 보유 원화
    volume: float = 0.0         # 보유 코인 수량
    avg_price: float = 0.0      # 평균 매수가
    realized_pnl: float = 0.0   # 누적 실현 손익
    trades: List[Trade] = field(default_factory=list)

    def has_position(self) -> bool:
        return self.volume > 1e-12


class StateStore:
    """Portfolio를 보관하고 thread-safe 하게 읽고/저장하는 컨테이너."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.portfolio = Portfolio()

    # ----- 영속화 -----
    def load(self, default_krw: float) -> None:
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    self.portfolio = Portfolio(
                        krw=raw.get("krw", default_krw),
                        volume=raw.get("volume", 0.0),
                        avg_price=raw.get("avg_price", 0.0),
                        realized_pnl=raw.get("realized_pnl", 0.0),
                        trades=[Trade(**t) for t in raw.get("trades", [])],
                    )
                    return
                except Exception:
                    pass
            # 파일이 없거나 손상 → 초기 자본으로 시작
            self.portfolio = Portfolio(krw=default_krw)

    def save(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            data = {
                "krw": self.portfolio.krw,
                "volume": self.portfolio.volume,
                "avg_price": self.portfolio.avg_price,
                "realized_pnl": self.portfolio.realized_pnl,
                "trades": [asdict(t) for t in self.portfolio.trades[-500:]],
            }
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # ----- 거래 기록 -----
    def record_buy(self, price: float, volume: float, fee: float) -> Trade:
        with self._lock:
            p = self.portfolio
            amount = price * volume
            # 평균 매수가 갱신
            total_cost = p.avg_price * p.volume + amount
            p.volume += volume
            p.avg_price = total_cost / p.volume if p.volume > 0 else 0.0
            trade = Trade(
                time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                side="buy", price=price, volume=volume, amount=amount, fee=fee,
            )
            p.trades.append(trade)
            return trade

    def record_sell(self, price: float, volume: float, fee: float) -> Trade:
        with self._lock:
            p = self.portfolio
            amount = price * volume
            # 실현 손익 = (매도가 - 평균매수가) * 수량 - 수수료
            pnl = (price - p.avg_price) * volume - fee
            p.realized_pnl += pnl
            p.volume -= volume
            if p.volume < 1e-12:
                p.volume = 0.0
                p.avg_price = 0.0
            trade = Trade(
                time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                side="sell", price=price, volume=volume, amount=amount,
                fee=fee, pnl=pnl,
            )
            p.trades.append(trade)
            return trade
