"""변동성 돌파 전략 (Larry Williams Volatility Breakout).

핵심 아이디어:
  - 전일 변동폭(range) = 전일 고가 - 전일 저가
  - 목표가(target) = 당일 시가 + range * k
  - 당일 현재가가 목표가를 돌파하면 매수
  - 다음 날 시가에 매도(청산)
  - (옵션) 이동평균 필터: 현재가가 MA 위에 있을 때만 매수하여 하락장 진입을 줄임
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    """전략 1회 평가 결과(대시보드 표시에도 사용)."""
    candle_date: str          # 현재 일봉 식별용 날짜 (YYYY-MM-DD)
    target_price: float       # 매수 목표가
    range_value: float        # 전일 변동폭
    today_open: float         # 당일 시가
    ma: Optional[float]       # 이동평균 (필터 미사용 시 None)
    ma_ok: bool               # 이동평균 필터 통과 여부
    breakout: bool            # 현재가가 목표가 돌파 여부
    should_buy: bool          # 최종 매수 신호 (돌파 + 필터 통과)


def evaluate(df, current: float, k: float, ma_period: int) -> Optional[Signal]:
    """일봉 DataFrame과 현재가로 매수 신호를 계산.

    df: pyupbit 일봉 (마지막 행이 '오늘' 진행 중인 봉)
    current: 현재가
    반환: Signal, 데이터 부족 시 None
    """
    if df is None or len(df) < 2:
        return None

    yesterday = df.iloc[-2]
    today = df.iloc[-1]

    range_value = float(yesterday["high"] - yesterday["low"])
    today_open = float(today["open"])
    target_price = today_open + range_value * k

    # 이동평균 필터: 직전 ma_period 개 종가의 평균. 전일까지의 종가 사용.
    ma: Optional[float] = None
    ma_ok = True
    if ma_period and ma_period > 0 and len(df) >= ma_period + 1:
        ma = float(df["close"].iloc[-(ma_period + 1):-1].mean())
        ma_ok = current > ma

    breakout = current >= target_price
    should_buy = breakout and ma_ok

    candle_date = str(df.index[-1].date())

    return Signal(
        candle_date=candle_date,
        target_price=target_price,
        range_value=range_value,
        today_open=today_open,
        ma=ma,
        ma_ok=ma_ok,
        breakout=breakout,
        should_buy=should_buy,
    )
