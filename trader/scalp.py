"""프로토타입3 (초단타) — 거래량 폭발 코인 모멘텀 스캘핑 로직.

전략: 거래량이 평소 대비 폭발한 코인 상위 N개만 골라, 돌파 모멘텀에 진입해
      +tp% 짧게 먹고 빠진다. 분할매수 없음(단일 진입), 코인당 고정 금액.

⚠️ 검증 한계(정직): 이 전략은 '펌핑하는 알트'를 노린다. BTC는 단기 모멘텀이 안 이어져
   BTC 백테스트는 전부 손실(엣지 없음) — 단 BTC는 P3엔 틀린 시험대다. 실제 알트(KRW)
   데이터로 검증 전엔 라이브 투입 금지. 고정 +tp%·손절·거래량배수는 전부 알트로 재튜닝 대상.

이 모듈은 '순수 로직'(랭킹·신호·백테스트)만 제공. 라이브 주문 연결은 알트 검증 후 별도.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd


def surge_ratio(df: pd.DataFrame, lookback: int = 20) -> float:
    """직전(닫힌) 봉 거래량 / 최근 lookback봉 평균 거래량. 클수록 '거래량 폭발'."""
    if df is None or len(df) < lookback + 2:
        return 0.0
    v = df["volume"]
    avg = v.iloc[-(lookback + 1):-1].mean()      # 직전 봉 제외한 최근 평균
    last = float(v.iloc[-2])                      # 직전 '닫힌' 봉
    return (last / avg) if avg > 0 else 0.0


def rank_top_volume(data: Dict[str, pd.DataFrame], top_n: int = 3,
                    vol_mult: float = 3.0, lookback: int = 20) -> List[str]:
    """여러 코인 중 거래량 폭발 상위 top_n 선정(배수 vol_mult 이상만)."""
    scored = []
    for tk, df in data.items():
        r = surge_ratio(df, lookback)
        if r >= vol_mult:
            scored.append((r, tk))
    scored.sort(reverse=True)
    return [tk for _, tk in scored[:top_n]]


def entry_signal(prev, row, cfg: dict) -> bool:
    """거래량 폭발 + 신고가 돌파(모멘텀) + 양봉이면 진입.
    row/prev = 닫힌 봉(직전2/직전3). breakout_high = 직전 brk봉 고가(미리 계산해 컬럼으로).
    """
    s = cfg.get("scalp_p3", {})
    vmult = float(s.get("vol_mult", 3.0))
    vavg = row.get("vol_avg", 0.0)
    if not vavg or vavg <= 0:
        return False
    surge = float(row["volume"]) >= vmult * float(vavg)
    breakout = float(row["close"]) > float(row.get("brk_high", float("inf")))
    green = float(row["close"]) > float(row["open"])
    return bool(surge and breakout and green)


def backtest_one(df: pd.DataFrame, cfg: dict) -> dict:
    """한 코인 시계열에 P3 스캘핑을 적용(단일진입·고정 TP/SL·타임아웃). 검증용."""
    s = cfg.get("scalp_p3", {})
    vmult = float(s.get("vol_mult", 3.0)); look = int(s.get("vol_lookback", 20))
    brk = int(s.get("breakout_lookback", 10)); tp = float(s.get("tp_pct", 0.01))
    sl = float(s.get("sl_pct", 0.007)); timeout = int(s.get("timeout_bars", 12))
    fee = float(cfg.get("trade", {}).get("fee", 0.0005)) * 2

    d = df.copy()
    d["vol_avg"] = d["volume"].rolling(look).mean().shift(1)
    d["brk_high"] = d["high"].rolling(brk).max().shift(1)
    o, h, l, c = d["open"].values, d["high"].values, d["low"].values, d["close"].values
    vol, vavg, bh = d["volume"].values, d["vol_avg"].values, d["brk_high"].values
    n = len(d); i = max(look, brk) + 2; trades = []
    while i < n - 1:
        ok = (vavg[i] > 0 and vol[i] >= vmult * vavg[i] and c[i] > bh[i] and c[i] > o[i])
        if ok and pd.notna(bh[i]):
            entry = c[i]; tpx = entry * (1 + tp); slx = entry * (1 - sl); j = i + 1; res = None
            while j < min(i + 1 + timeout, n):
                if l[j] <= slx: res = -sl; break
                if h[j] >= tpx: res = tp; break
                j += 1
            if res is None:
                res = c[min(i + timeout, n - 1)] / entry - 1
            trades.append(res - fee); i = j + 1
        else:
            i += 1
    import numpy as np
    a = np.array(trades) if trades else np.array([0.0])
    return {"n": len(trades), "winrate": float((a > 0).mean() * 100),
            "avg": float(a.mean() * 100), "total": float(a.sum() * 100)}
