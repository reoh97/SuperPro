"""주문 실행 추상화: 모의(PaperBroker)와 실거래(LiveBroker).

엔진은 Broker 인터페이스에만 의존하므로, mode 설정만 바꾸면
같은 전략 코드로 모의/실거래를 전환할 수 있습니다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .state import StateStore


@dataclass
class Fill:
    price: float
    volume: float
    fee: float


class Broker:
    """주문 인터페이스."""

    def krw_balance(self) -> float:
        raise NotImplementedError

    def coin_balance(self) -> float:
        raise NotImplementedError

    def buy(self, price: float, krw_amount: float) -> Optional[Fill]:
        """krw_amount(원) 만큼 시장가 매수. 체결 정보 반환."""
        raise NotImplementedError

    def sell(self, price: float, volume: float) -> Optional[Fill]:
        """volume(코인 수량) 만큼 시장가 매도. 체결 정보 반환."""
        raise NotImplementedError


class PaperBroker(Broker):
    """가상 잔고로 주문을 시뮬레이션. 현재가에 즉시 체결되었다고 가정하고 수수료만 차감."""

    def __init__(self, store: StateStore, fee: float):
        self.store = store
        self.fee = fee

    def krw_balance(self) -> float:
        return self.store.portfolio.krw

    def coin_balance(self) -> float:
        return self.store.portfolio.volume

    def buy(self, price: float, krw_amount: float) -> Optional[Fill]:
        p = self.store.portfolio
        krw_amount = min(krw_amount, p.krw)
        if krw_amount <= 0 or price <= 0:
            return None
        fee = krw_amount * self.fee
        volume = (krw_amount - fee) / price        # 수수료 제외 후 코인 매수
        p.krw -= krw_amount
        self.store.record_buy(price, volume, fee)
        self.store.save()
        return Fill(price=price, volume=volume, fee=fee)

    def sell(self, price: float, volume: float) -> Optional[Fill]:
        p = self.store.portfolio
        volume = min(volume, p.volume)
        if volume <= 0 or price <= 0:
            return None
        gross = price * volume
        fee = gross * self.fee
        p.krw += gross - fee                        # 매도대금 입금(수수료 차감)
        self.store.record_sell(price, volume, fee)
        self.store.save()
        return Fill(price=price, volume=volume, fee=fee)


class LiveBroker(Broker):
    """실제 업비트 계정에서 시장가 주문 실행. (live 모드)

    주의: 실제 자산이 거래됩니다. 충분히 모의로 검증한 뒤 사용하세요.
    """

    def __init__(self, access_key: str, secret_key: str, ticker: str,
                 store: StateStore, fee: float):
        import pyupbit  # live 모드에서만 인증 객체 필요
        access = access_key or os.environ.get("UPBIT_ACCESS_KEY", "")
        secret = secret_key or os.environ.get("UPBIT_SECRET_KEY", "")
        if not access or not secret:
            raise RuntimeError(
                "실거래(live) 모드에는 업비트 API 키가 필요합니다. "
                "config.yaml 또는 환경변수 UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY 를 설정하세요."
            )
        self.upbit = pyupbit.Upbit(access, secret)
        self.ticker = ticker
        self.currency = ticker.split("-")[1]   # 예: KRW-BTC -> BTC
        self.store = store
        self.fee = fee

    def krw_balance(self) -> float:
        return float(self.upbit.get_balance("KRW") or 0.0)

    def coin_balance(self) -> float:
        return float(self.upbit.get_balance(self.ticker) or 0.0)

    def buy(self, price: float, krw_amount: float) -> Optional[Fill]:
        if krw_amount <= 0:
            return None
        # 업비트 시장가 매수는 '주문 금액(원)'을 지정
        resp = self.upbit.buy_market_order(self.ticker, krw_amount)
        if not resp or "error" in resp:
            return None
        fee = krw_amount * self.fee
        volume = (krw_amount - fee) / price   # 근사 추정(실제 체결가는 평균가로 달라질 수 있음)
        self.store.record_buy(price, volume, fee)
        self.store.save()
        return Fill(price=price, volume=volume, fee=fee)

    def sell(self, price: float, volume: float) -> Optional[Fill]:
        if volume <= 0:
            return None
        resp = self.upbit.sell_market_order(self.ticker, volume)
        if not resp or "error" in resp:
            return None
        gross = price * volume
        fee = gross * self.fee
        self.store.record_sell(price, volume, fee)
        self.store.save()
        return Fill(price=price, volume=volume, fee=fee)
