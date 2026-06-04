"""업비트 시세 조회 래퍼.

공개 시세 API만 사용하므로 API 키가 필요 없습니다. (모의/실거래 공통)
pyupbit 호출을 한 곳으로 모아 두어, 네트워크 오류 시 재시도/예외 처리를 일관되게 합니다.
"""
from __future__ import annotations

import time
from typing import Optional

import pyupbit


def current_price(ticker: str, retries: int = 3) -> Optional[float]:
    """현재가(원). 실패 시 None."""
    for i in range(retries):
        try:
            price = pyupbit.get_current_price(ticker)
            if price:
                return float(price)
        except Exception:
            pass
        time.sleep(0.3 * (i + 1))
    return None


def daily_ohlcv(ticker: str, count: int = 10, retries: int = 3):
    """일봉 OHLCV(pandas DataFrame). index 는 KST 기준 날짜.

    반환 컬럼: open, high, low, close, volume, value
    실패 시 None.
    """
    for i in range(retries):
        try:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=count)
            if df is not None and len(df) > 0:
                return df
        except Exception:
            pass
        time.sleep(0.3 * (i + 1))
    return None
