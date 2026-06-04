"""국면별 전략 — 국면 성격에 '맞는' 방식으로 진입/청산.

  - 상승장(UP)   : 추세추종 — 눌림목 복귀에서 매수, '트레일링 스탑'으로 수익을 길게.
  - 횡보장(SIDE) : 평균회귀 — 볼린저 하단+과매도 매수, '고정 TP/SL'(박스권에 적합).
  - 하락장(DOWN) : 관망 — 신규 매수 안 함(현금 보유). 현물은 공매도 불가.

청산 모드:
  - "fixed": 고정 익절(TP)/손절(SL)  → 횡보장
  - "trail": 최초 손절(sl_pct) + 고점 대비 트레일링(trail_pct) → 상승장(수익 끌기)
"""
from __future__ import annotations

from dataclasses import dataclass

from . import regime


@dataclass
class Entry:
    should_enter: bool
    exit_mode: str = "fixed"   # "fixed" | "trail"
    tp_pct: float = 0.0        # fixed 모드 익절
    sl_pct: float = 0.0        # 손절(최초 손절선)
    trail_pct: float = 0.0     # trail 모드: 고점 대비 트레일링 폭
    reason: str = ""


_NO = Entry(False)


def _min_tp(cfg: dict, fee: float) -> float:
    buffer = float(cfg.get("scalp", {}).get("min_profit_buffer", 0.002))
    return fee * 2 + buffer  # 왕복 수수료 + 버퍼


def _range_entry(row, s: dict, cfg: dict, fee: float) -> Entry:
    """박스권 발라먹기: 하단 근처 매수 → 상단 근처 익절, 박스 이탈 시 손절."""
    import math
    top, bot, close = row["box_top"], row["box_bot"], row["close"]
    if not (math.isfinite(top) and math.isfinite(bot)) or bot <= 0 or top <= bot:
        return _NO

    height = top - bot
    height_pct = height / bot
    min_box = float(s.get("min_box_pct", 0.015))   # 너무 작은 박스는 수수료 대비 미미
    max_box = float(s.get("max_box_pct", 0.06))     # 너무 크면 추세로 간주
    if height_pct < min_box or height_pct > max_box:
        return _NO

    # 하단 근처(박스높이의 entry_zone 이내)에서만 매수
    entry_zone = float(s.get("entry_zone", 0.25))
    if close > bot + entry_zone * height:
        return _NO

    # 익절: 상단 근처(tp_zone 지점), 손절: 박스 하단 아래 sl_below
    tp_zone = float(s.get("tp_zone", 0.85))
    sl_below = float(s.get("sl_below", 0.004))
    tp_target = bot + tp_zone * height
    sl_target = bot * (1 - sl_below)

    tp_pct = (tp_target - close) / close
    sl_pct = (close - sl_target) / close
    if tp_pct < _min_tp(cfg, fee) or sl_pct <= 0:
        return _NO  # 상단까지 여유가 수수료보다 작거나 손절선이 위면 스킵

    return Entry(True, "fixed", tp_pct, sl_pct, 0.0,
                 f"박스권 하단매수(폭 {height_pct*100:.1f}%)")


def evaluate(prev, row, reg: str, cfg: dict, fee: float) -> Entry:
    """직전 봉(prev)·현재 봉(row)·국면(reg)으로 진입 판단."""
    scfg = cfg.get("scalp", {})

    if reg == regime.UP:
        s = scfg.get("uptrend", {})
        if not bool(s.get("enabled", True)):
            return _NO  # 상승장 추세진입 끄기(박스권만 검증할 때)
        # 눌림목 복귀: 직전엔 ema_fast 아래 → 현재 위로 복귀(추세 재개 시 매수)
        if prev["close"] <= prev["ema_fast"] and row["close"] > row["ema_fast"]:
            # 진입 품질 필터(가짜추세 컷) — 켜진 것만 적용
            if s.get("require_adx_rising", False) and not (row["adx"] > prev["adx"]):
                return _NO  # 추세 강도가 약해지는 중이면 진입 보류
            if s.get("require_bull_candle", False) and not (row["close"] > row["open"]):
                return _NO  # 진입봉이 음봉이면 보류
            if row["adx"] < float(s.get("min_adx", 0)):
                return _NO  # 추세 강도 최소기준 미달
            if s.get("require_above_mid", False) and not (row["close"] > row["ema_mid"]):
                return _NO  # 중기 EMA 위(더 강한 추세 위치)에서만
            mode = s.get("mode", "trail")
            if mode == "fixed":
                # 1% 자주먹기: 고정 익절(수수료 제외 ≈순익 tp_pct) + 고정 손절, 물타기 없음(단일진입)
                tp = max(float(s.get("tp_pct", 0.011)), _min_tp(cfg, fee))
                sl = float(s.get("sl_pct", 0.015))
                return Entry(True, "scalp", tp, sl, 0.0, "상승장 단타(고정 익절)")
            sl = float(s.get("sl_pct", 0.02))
            trail = float(s.get("trail_pct", 0.025))
            return Entry(True, "trail", 0.0, sl, trail, "상승장 추세추종(트레일링)")
        return _NO

    if reg == regime.SIDEWAYS:
        s = scfg.get("sideways", {})
        mode = s.get("mode", "range")

        if mode == "range":
            return _range_entry(row, s, cfg, fee)

        # (대안) 단순 평균회귀: 볼린저 하단 + RSI 과매도
        tp = max(float(s.get("tp_pct", 0.005)), _min_tp(cfg, fee))
        sl = float(s.get("sl_pct", 0.004))
        rsi_low = float(s.get("rsi_buy", 32))
        if s.get("require_above_slow", True) and row["close"] <= row["ema_slow"]:
            return _NO
        if row["close"] <= row["bb_low"] and row["rsi"] < rsi_low:
            return Entry(True, "fixed", tp, sl, 0.0, "횡보장 평균회귀")
        return _NO

    if reg == regime.DOWN:
        s = scfg.get("downtrend", {})
        if not bool(s.get("enabled", False)):
            return _NO  # 하락장: 관망(현금 보유)
        tp = max(float(s.get("tp_pct", 0.005)), _min_tp(cfg, fee))
        sl = float(s.get("sl_pct", 0.003))
        rsi_low = float(s.get("rsi_buy", 25))
        if row["rsi"] < rsi_low and row["close"] > prev["close"] and row["close"] > row["open"]:
            return Entry(True, "fixed", tp, sl, 0.0, "하락장 과매도 반등 단타")
        return _NO

    return _NO
