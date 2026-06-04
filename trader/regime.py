"""시장 국면(상승장/하락장/횡보장) 판별 — 기술적 지표 기반.

AI 하이브리드의 1차 판단을 담당합니다. (실거래 엔진에서는 애매한 구간일 때
Claude가 보조 판단/뉴스 거부권으로 개입하지만, 백테스트는 이 기술적 판단만 사용)

판별 로직:
  - 추세 강도(ADX)가 약하면 → 횡보장 (SIDEWAYS)
  - ADX가 강하고 EMA 정배열 + 가격>장기EMA → 상승장 (UP)
  - ADX가 강하고 EMA 역배열 + 가격<장기EMA → 하락장 (DOWN)
  - 그 외(추세는 강하나 방향 불명확) → 횡보장
"""
from __future__ import annotations

UP = "UP"
DOWN = "DOWN"
SIDEWAYS = "SIDEWAYS"


def classify(row, cfg: dict) -> str:
    """지표가 채워진 한 행(row)을 받아 국면 문자열 반환."""
    rcfg = cfg.get("regime", {})
    adx_th = float(rcfg.get("adx_trend", 20))

    adx = row["adx"]
    close = row["close"]
    ef, em, es = row["ema_fast"], row["ema_mid"], row["ema_slow"]

    # 추세가 약하면 횡보
    if adx < adx_th:
        return SIDEWAYS

    up_aligned = ef > em and close > es
    down_aligned = ef < em and close < es

    if up_aligned and row["plus_di"] >= row["minus_di"]:
        return UP
    if down_aligned and row["minus_di"] >= row["plus_di"]:
        return DOWN
    return SIDEWAYS
