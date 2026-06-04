"""기술적 지표 (순수 pandas/numpy 구현, TA-Lib 등 외부 의존성 없음).

모두 '인과적(causal)' 지표 — i번째 값은 i 시점까지의 데이터만 사용하므로
전체 DataFrame에 한 번 계산해 두고 백테스트에서 행 인덱스로 읽어도 미래참조가 없습니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (Wilder)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    """ADX + +DI/-DI (Wilder). 반환: (adx, plus_di, minus_di)."""
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0.0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_ = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx_.fillna(0.0), plus_di.fillna(0.0), minus_di.fillna(0.0)


def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0):
    """볼린저밴드. 반환: (mid, upper, lower, width_pct)."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + mult * std
    lower = mid - mult * std
    width_pct = (upper - lower) / mid.replace(0.0, np.nan)  # 밴드 폭(중심선 대비 비율)
    return mid, upper, lower, width_pct


def enrich(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """전략에 필요한 모든 지표를 컬럼으로 추가해 반환."""
    out = df.copy()
    ind = cfg.get("indicators", {})
    out["ema_fast"] = ema(out["close"], ind.get("ema_fast", 20))
    out["ema_mid"] = ema(out["close"], ind.get("ema_mid", 50))
    out["ema_slow"] = ema(out["close"], ind.get("ema_slow", 200))
    out["rsi"] = rsi(out["close"], ind.get("rsi", 14))
    out["atr"] = atr(out["high"], out["low"], out["close"], ind.get("atr", 14))
    adx_, pdi, mdi = adx(out["high"], out["low"], out["close"], ind.get("adx", 14))
    out["adx"], out["plus_di"], out["minus_di"] = adx_, pdi, mdi
    mid, up, lo, width = bollinger(out["close"], ind.get("bb", 20), ind.get("bb_mult", 2.0))
    out["bb_mid"], out["bb_up"], out["bb_low"], out["bb_width"] = mid, up, lo, width

    # 박스권(레인지) 상/하단: 최근 lookback 봉의 고점/저점.
    # shift(1)로 '직전까지' 형성된 박스를 사용 (현재봉으로 박스를 정의하는 미래참조 방지)
    look = int(cfg.get("scalp", {}).get("sideways", {}).get("box_lookback", 40))
    out["box_top"] = out["high"].rolling(look).max().shift(1)
    out["box_bot"] = out["low"].rolling(look).min().shift(1)
    return out
