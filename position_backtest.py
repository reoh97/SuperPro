"""중장기 추세추종(포지션 트레이딩) 백테스트 — 돈키언 돌파 + 장기추세 필터 + ATR 넓은 청산.

단타봇이 놓치는 '몇 주~몇 달짜리 큰 추세'를 끝까지 타는 전략.
  진입: 가격 > 장기EMA(상승추세) AND 최근 entry_n봉 고점 돌파(모멘텀)
  청산: 최근 exit_n봉 저점 이탈(추세붕괴) OR 고점 대비 ATR×k 되돌림(샹들리에)
  → 하락추세선 아래면 진입 안 함 = 하락장 자동 회피(숏 없이 현금).

※ 이 데이터(국면별 1~2개월)는 중장기엔 짧음 → 개념 확인용. 진짜 검증은 일봉 1~3년 필요(fetch_daily).
사용법: python position_backtest.py
"""
from __future__ import annotations
import os, sys, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuit_backtest as cb
from trader import indicators

FEE = 0.0005

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def run_coin(e, entry_n, exit_n, atr_k):
    """돈키언 추세추종 1종목 → (수익률%, 거래수, 평균보유봉, 최대낙폭%)."""
    c = e["close"].to_numpy(float); h = e["high"].to_numpy(float)
    lo = e["low"].to_numpy(float); es = e["ema_slow"].to_numpy(float)
    atr = e["atr"].to_numpy(float)
    cap = 1_000_000.0; eq = cap; pos = None; trades = []; holds = []
    peak_eq = cap; mdd = 0.0
    for i in range(210, len(c)):
        # 보유 중: 청산 감시
        if pos is not None:
            pos["peak"] = max(pos["peak"], h[i])
            chand = pos["peak"] - atr_k * atr[i]                 # 샹들리에 손절(고점-ATR×k)
            donch_exit = lo[i] <= np.min(lo[i-exit_n:i]) if i > exit_n else False
            if c[i] <= chand or donch_exit:
                eq = pos["sz"] * c[i] * (1 - FEE)
                trades.append(eq/pos["cost_eq"]-1); holds.append(i-pos["i0"]); pos = None
        # 미보유: 진입 (상승추세 + 돈키언 고점돌파)
        elif i > entry_n and c[i] > es[i] and c[i] >= np.max(h[i-entry_n:i]):
            pos = {"sz": eq*(1-FEE)/c[i], "cost_eq": eq, "peak": h[i], "i0": i}
            eq = eq  # 전액 투입(평가액 유지)
        cur = (pos["sz"]*c[i] if pos else eq)
        peak_eq = max(peak_eq, cur); mdd = max(mdd, (peak_eq-cur)/peak_eq)
    if pos is not None:
        eq = pos["sz"]*c[-1]*(1-FEE); holds.append(len(c)-pos["i0"])
    final = (pos["sz"]*c[-1] if pos else eq)
    return (final/cap-1)*100, len(trades)+(1 if pos else 0), (np.mean(holds) if holds else 0), mdd*100


cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"config.yaml"),encoding="utf-8"))
print("="*70)
print("  중장기 추세추종(돈키언) — 개념검증  ※국면별 1~2개월 데이터(짧음)")
print("  진입: 상승추세+고점돌파 / 청산: 저점이탈 or 샹들리에(고점-ATR×4)")
print("="*70)
# 파라미터: entry_n(고점돌파 구간), exit_n(저점이탈), atr_k(샹들리에)
P = {"entry_n": 96, "exit_n": 48, "atr_k": 4.0}    # 96봉=1일 고점돌파, 48봉 저점이탈
for label, nm in (("bull","상승"),("sideways","횡보"),("bear","약세")):
    data = cb.load_csv(label, cfg)
    rets=[]; mdds=[]; holds=[]; ns=[]
    for tk, df in data.items():
        e = indicators.enrich(df, cfg)
        r, n, hold, mdd = run_coin(e, **P)
        rets.append(r); mdds.append(mdd); holds.append(hold); ns.append(n)
    rets=np.array(rets)
    print(f"\n  [{nm}] 평균수익 {rets.mean():+.2f}%/코인  +코인 {(rets>0).mean()*100:.0f}%  "
          f"평균거래 {np.mean(ns):.1f}회  평균보유 {np.mean(holds)/96:.1f}일  평균MDD {np.mean(mdds):.1f}%")
print("\n  비교: 단타봇 불장 +3.68%/횡보+1.62%/약세 0%. 추세추종이 큰추세를 더 먹나?")
print("  ※ 진짜 판단은 일봉 1~3년 데이터로(fetch_daily). 지금은 '길게 들면 다른가' 감만.")
