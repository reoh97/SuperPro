"""다지표 합의(Confluence) 진입 엔진.

사람은 동시에 못 보는 '모든 기술적 지표'를 한 표씩 모아 → 그룹별로 정규화(상관 중복
제거) → 국면별 가중치로 합산 → 진입 점수(0~100). 문턱을 넘을 때만 진입한다.

설계 원칙(중요):
  - 지표를 그냥 다 더하면 RSI/스토캐스틱/MACD처럼 서로 상관 높은 것들이 같은 의견을
    중복 카운트해 편향된다. → '그룹'으로 묶어 그룹 내 평균(-1~+1)을 그 그룹의 1표로 정규화.
  - 그룹: trend(추세) / momentum(모멘텀) / meanrev(평균회귀) / volatility(변동성) / volume(거래량).
  - 국면(UP/SIDEWAYS/DOWN)마다 그룹 가중치를 다르게 → 상승장은 추세·모멘텀 가중↑,
    횡보장은 평균회귀 가중↑. (config.confluence.weights)
  - 점수 = Σ(그룹표 × 가중) / Σ가중  →  [-1,1]을 [0,100]으로 사상한 '매수 관점' 점수.

각 그룹 함수는 [-1,+1] 실수(매도~매수)를 반환하거나, 계산 불가 시 None(가중 0 처리).
모든 지표는 인과적(현재 봉까지의 데이터만) — indicators.enrich가 미리 컬럼으로 채워둔다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class Score:
    score: float                 # 0~100 (매수 관점). 50=중립
    raw: float                   # -1~+1 (가중평균 원점수)
    groups: dict                 # 그룹별 표 {name: vote or None}
    passed: bool                 # score >= threshold


def _avg(votes) -> Optional[float]:
    """None을 제외한 표들의 평균. 전부 None이면 None."""
    vals = [v for v in votes if v is not None]
    return sum(vals) / len(vals) if vals else None


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


# ---------------- 그룹별 투표 ----------------

def _trend(prev, row, cfg) -> Optional[float]:
    """추세: EMA 정배열, 가격 vs 장기EMA, ADX+DI 방향, MACD 라인."""
    v = []
    ef, em, es = row["ema_fast"], row["ema_mid"], row["ema_slow"]
    if ef > em > es:
        v.append(1.0)
    elif ef < em < es:
        v.append(-1.0)
    else:
        v.append(0.0)
    v.append(1.0 if row["close"] > es else -1.0)
    adx_th = float(cfg.get("regime", {}).get("adx_trend", 20))
    if row["adx"] >= adx_th:                       # 추세가 강할 때만 DI 방향을 표로
        v.append(1.0 if row["plus_di"] >= row["minus_di"] else -1.0)
    else:
        v.append(0.0)
    v.append(1.0 if row["macd"] > row["macd_signal"] else -1.0)
    return _avg(v)


def _momentum(prev, row, cfg) -> Optional[float]:
    """모멘텀: RSI 위치+방향, 스토캐스틱 교차, MACD 히스토그램."""
    v = []
    if row["rsi"] > 50 and row["rsi"] > prev["rsi"]:
        v.append(1.0)
    elif row["rsi"] < 50 and row["rsi"] < prev["rsi"]:
        v.append(-1.0)
    else:
        v.append(0.0)
    k, d = row["stoch_k"], row["stoch_d"]
    if k > d and k < 80:                           # 상향 교차(과열 80 미만)
        v.append(1.0)
    elif k < d and k > 20:                         # 하향 교차(침체 20 초과)
        v.append(-1.0)
    else:
        v.append(0.0)
    if row["macd_hist"] > 0 and row["macd_hist"] > prev["macd_hist"]:
        v.append(1.0)
    elif row["macd_hist"] < 0 and row["macd_hist"] < prev["macd_hist"]:
        v.append(-1.0)
    else:
        v.append(0.0)
    return _avg(v)


def _meanrev(prev, row, cfg) -> Optional[float]:
    """평균회귀(저가매수/과열매도): 볼린저 위치, RSI 과매도 반등."""
    v = []
    if _finite(row["bb_low"]) and _finite(row["bb_up"]):
        if row["close"] <= row["bb_low"]:
            v.append(1.0)                          # 하단 = 저가 매수 기회
        elif row["close"] >= row["bb_up"]:
            v.append(-1.0)                         # 상단 = 과열
        else:
            v.append(0.0)
    rsi_lo = float(cfg.get("confluence", {}).get("meanrev_rsi_low", 35))
    rsi_hi = float(cfg.get("confluence", {}).get("meanrev_rsi_high", 70))
    if row["rsi"] < rsi_lo and row["close"] > prev["close"]:
        v.append(1.0)                              # 과매도 + 반등 시작
    elif row["rsi"] > rsi_hi:
        v.append(-1.0)
    else:
        v.append(0.0)
    return _avg(v)


def _volatility(prev, row, cfg) -> Optional[float]:
    """변동성(진입 적합도): ATR이 적정대인가, 볼린저 수축→확장(스퀴즈 돌파)."""
    v = []
    ccfg = cfg.get("confluence", {})
    if _finite(row["atr"]) and row["close"]:
        atr_pct = float(row["atr"]) / float(row["close"])
        lo = float(ccfg.get("atr_pct_min", 0.002))
        hi = float(ccfg.get("atr_pct_max", 0.030))
        if lo <= atr_pct <= hi:
            v.append(1.0)                          # 먹을 만한 변동성
        elif atr_pct < lo:
            v.append(-1.0)                         # 변동성 고갈(움직임 없음)
        else:
            v.append(0.0)                          # 과변동 = 위험하나 기회도(중립)
    if _finite(row["bb_width"]) and _finite(prev["bb_width"]):
        v.append(0.5 if row["bb_width"] > prev["bb_width"] else 0.0)  # 확장=돌파 잠재
    return _avg(v)


def _volume(prev, row, cfg) -> Optional[float]:
    """거래량(진정성): 평균 대비 거래량, OBV 추세. volume 결측 시 None(그룹 제외)."""
    if "vol_ma" not in row.index or not _finite(row.get("vol_ma")):
        return None
    v = []
    vm = float(row["vol_ma"])
    if vm > 0:
        if row["volume"] > vm * 1.2:
            v.append(1.0)                          # 평균 대비 거래량 동반(진정한 움직임)
        elif row["volume"] < vm * 0.7:
            v.append(-1.0)                         # 참여 없음(가짜 움직임 의심)
        else:
            v.append(0.0)
    if _finite(row.get("obv")) and _finite(row.get("obv_ema")):
        v.append(1.0 if row["obv"] > row["obv_ema"] else -1.0)
    return _avg(v)


_GROUPS = {
    "trend": _trend,
    "momentum": _momentum,
    "meanrev": _meanrev,
    "volatility": _volatility,
    "volume": _volume,
}

# 가중치 미설정 국면용 기본값(균등)
_DEFAULT_WEIGHTS = {
    "UP":       {"trend": 2.0, "momentum": 1.5, "meanrev": 0.0, "volatility": 0.5, "volume": 1.0},
    "SIDEWAYS": {"trend": 0.5, "momentum": 1.0, "meanrev": 2.0, "volatility": 0.5, "volume": 1.0},
    "DOWN":     {"trend": 1.0, "momentum": 1.0, "meanrev": 1.5, "volatility": 0.5, "volume": 1.0},
}


def score(prev, row, reg: str, cfg: dict) -> Score:
    """직전 봉·현재 봉·국면으로 합의 점수를 계산."""
    ccfg = cfg.get("confluence", {})
    weights = ccfg.get("weights", {}).get(reg) or _DEFAULT_WEIGHTS.get(reg, {})

    groups = {name: fn(prev, row, cfg) for name, fn in _GROUPS.items()}

    num = den = 0.0
    for name, vote in groups.items():
        if vote is None:                            # 계산 불가(예: 거래량 결측) → 가중 0
            continue
        w = float(weights.get(name, 0.0))
        if w == 0.0:
            continue
        num += vote * w
        den += w
    raw = (num / den) if den > 0 else 0.0           # [-1,1]
    sc = (raw + 1.0) * 50.0                          # [0,100]
    threshold = float(ccfg.get("threshold", 70))
    return Score(sc, raw, groups, sc >= threshold)
