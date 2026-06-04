"""시장(차트) 요약 — AI 국면판단 입력용 공용 헬퍼.

차트 지표를 사람이 읽을 수 있는 요약 문장으로 만든다. market_regime.py(단독도구)와
live.py(라이브 엔진)가 함께 사용한다.
"""
from __future__ import annotations

from . import indicators, regime


def build_market_summary(df, cfg: dict) -> str:
    """enrich 가능한 OHLCV df → AI 입력용 차트 요약 문자열."""
    d = indicators.enrich(df, cfg)
    row = d.iloc[-1]
    reg = regime.classify(row, cfg)
    reg_kr = {"UP": "상승추세", "DOWN": "하락추세", "SIDEWAYS": "방향성약함/횡보"}[reg]

    close = float(row["close"])
    look_d = min(96, len(d) - 1)   # 약 1일(15m*96)
    look_h = min(24, len(d) - 1)   # 약 6시간
    chg_d = (close / float(d.iloc[-1 - look_d]["close"]) - 1) * 100
    chg_h = (close / float(d.iloc[-1 - look_h]["close"]) - 1) * 100

    ef, em, es = float(row["ema_fast"]), float(row["ema_mid"]), float(row["ema_slow"])
    align = "정배열(상승구조)" if ef > em > es else "역배열(하락구조)" if ef < em < es else "혼조"
    return (
        f"- 차트 기술적 국면: {reg_kr} (ADX {float(row['adx']):.0f})\n"
        f"- 현재가: {close:,.0f}원\n"
        f"- 최근 약1일 변화: {chg_d:+.2f}%, 최근 약6시간: {chg_h:+.2f}%\n"
        f"- EMA 배열: {align} (단기 {ef:,.0f} / 중기 {em:,.0f} / 장기 {es:,.0f})\n"
        f"- RSI: {float(row['rsi']):.0f}, 가격 vs 장기EMA: {'위' if close > es else '아래'}"
    )
