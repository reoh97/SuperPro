"""lead-lag 정밀분석(1분봉) — 바이낸스 급등의 '동시분 vs 다음분' 분해.
핵심: 바이낸스가 분T에 급등 → 업비트가 같은 분T에 이미 따라가나(엣지X) /
      다음 분T+1에 더 따라와 수수료 넘게 먹히나(엣지O). 회의적으로 본다."""
from __future__ import annotations
import sys, glob, os, numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
FEE_RT = 0.001  # 왕복 수수료 0.1% (메이커면 더 낮음)

def analyze(path):
    coin = os.path.basename(path)[:-4]
    g = pd.read_csv(path, index_col=0, parse_dates=True)[["binance","upbit"]].dropna()
    bn = np.log(g["binance"]).diff().fillna(0.0)
    up = np.log(g["upbit"]).diff().fillna(0.0)
    # 1) 교차상관 (바이낸스 lag분 선행 vs 업비트)
    cc = {lag: bn.shift(lag).corr(up) for lag in range(0,6)}
    best_lag = max(cc, key=cc.get)
    # 2) 이벤트 스터디 — 여러 임계(σ배수)로, 동시분/다음분 분해
    print(f"\n[{coin}] {len(g):,}분  교차상관 " + " ".join(f"{k}분={v:+.2f}" for k,v in cc.items())
          + f"  → 최대 {best_lag}분")
    std = bn.std()
    idx = g.index
    for k in (3,5,8):
        thr = std*k
        hits = bn[bn>thr].index
        if len(hits) < 5:
            print(f"   {k}σ(>{thr*100:.2f}%): 이벤트 {len(hits)}회 — 부족"); continue
        same, nxt = [], []   # 동시분 업비트수익, 다음분 업비트수익
        for t in hits:
            p = idx.get_indexer([t])[0]
            if p < 1 or p >= len(g)-1: continue
            same.append(g["upbit"].iloc[p]/g["upbit"].iloc[p-1]-1)  # 분T 업비트(동시)
            nxt.append(g["upbit"].iloc[p+1]/g["upbit"].iloc[p]-1)   # 분T+1 업비트(추종)
        same, nxt = np.array(same), np.array(nxt)
        bn_jump = bn[bn>thr].reindex(pd.Index([t for t in hits if idx.get_indexer([t])[0]>=1 and idx.get_indexer([t])[0]<len(g)-1])).mean()
        net = nxt.mean()-FEE_RT
        print(f"   {k}σ(>{thr*100:.2f}%, {len(nxt)}회): "
              f"업비트 동시분 {same.mean()*100:+.3f}% | 다음분 {nxt.mean()*100:+.3f}% "
              f"(수수료차감 {net*100:+.3f}%, +비율 {(nxt>0).mean()*100:.0f}%) "
              f"{'★엣지' if net>0 else '✗'}")

print("="*70)
print("  lead-lag 정밀분석 — 바이낸스 급등 직후 업비트 '동시분 vs 다음분'")
print(f"  왕복수수료 {FEE_RT*100:.1f}% 차감.  '다음분' 순수익이 +라야 우리가 먹을 수 있음")
print("="*70)
for p in sorted(glob.glob("leadlag_data/KRW-*.csv")):
    analyze(p)
print("\n판정: '다음분 수수료차감'이 일관되게 +면 빠른봇 가치 / 0·- 면 프로가 이미 먹음→포기")
