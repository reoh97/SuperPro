"""동적 종목 선별 — '지금 막 치고 나가는 코인'을 미래정보 없이 골라 상위 K개만 매매.

고정 바스켓 대신 주기적으로 후보 유니버스를 점수화해 상위 K만 활성 종목으로 쓴다.

신호 선정 근거(signal_scan.py 실측, 불장+횡보+약세 롤링 재선별):
  - 효율비율(ER)은 무용지물(±0.1%p) — '이미 추세 중'은 '앞으로 갈 코인'을 예측 못 함.
  - **변동성조정 1일 모멘텀**(MOM/σ)이 유일하게 3국면 전부 양수(불장+0.69·횡보+0.11·약세+0.03%p).
    = '최근 위험대비 강하게 움직인 코인을 잡아탄다' → '제대로 된 한 방' 철학과 부합.
  - top_k: 불장은 1이 최강(집중), 횡보는 2~3이 안전(분산). 기본 3(안정), 설정으로 조절.

라이브에선 직전 N봉만 있으면 계산되므로 lookahead 없이 그대로 실시간 적용된다.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def momentum_vol(close: pd.Series, period: int = 96) -> float:
    """변동성조정 모멘텀 = (period봉 수익) / (봉수익률 표준편차). 위험대비 추세 강도."""
    if len(close) < period + 2:
        return float("-inf")
    seg = close.iloc[-(period + 1):].to_numpy(dtype=float)
    mom = seg[-1] / seg[0] - 1.0
    rets = np.diff(seg) / seg[:-1]
    vol = float(np.std(rets))
    return float(mom / vol) if vol > 0 else float("-inf")


def fitness(df: pd.DataFrame, period: int = 96) -> float:
    """종목 적합도 = 변동성조정 모멘텀. 높을수록 '한 방' 후보."""
    return momentum_vol(df["close"], period)


def rank_coins(data: Dict[str, pd.DataFrame], top_k: int = 3, period: int = 96) -> List[str]:
    """후보 데이터(코인→직전봉 DataFrame) → 적합도 상위 top_k 티커."""
    scores = {tk: fitness(df, period)
              for tk, df in data.items() if df is not None and len(df) > period + 2}
    ranked = sorted(scores, key=lambda t: scores[t], reverse=True)
    return ranked[:top_k]


def score_table(data: Dict[str, pd.DataFrame], period: int = 96) -> Dict[str, float]:
    """디버그/모니터용: 코인별 적합도 점수 dict."""
    return {tk: fitness(df, period) for tk, df in data.items() if df is not None}
