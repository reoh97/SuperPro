"""교차 모멘텀(상대강도 로테이션) 엣지 검증 — 살아남은 3엔진과 축이 다른가?

코어=한 코인의 절대 추세(시계열 모멘텀). 이건 그게 아니라
'10개 코인 중 상대적으로 가장 센 놈 top-k를 골라 주기적으로 갈아타기'(횡단면 모멘텀).
핵심 질문: 순위로 고르는 게 그냥 전부 동일가중으로 들고 있는 것보다 알파가 있나?
없으면(그냥 베타면) 깔끔히 닫는다. 비용(수수료+슬리피지) 넣고도 +라야 가치.

데이터: daily_data/(10코인 일봉 3년). BTC 200MA+기울기로 약세 게이트(현금).
"""
from __future__ import annotations
import sys, numpy as np, pandas as pd, yaml

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

COINS = ["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA",
         "KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
FEE = 0.0005
cfg = yaml.safe_load(open("config.yaml"))
SLIP = {**{c: cfg["slippage"]["by_coin"].get(c, cfg["slippage"]["default_pct"]) for c in COINS}}

# 정렬된 종가 패널
px = {}
for c in COINS:
    df = pd.read_csv(f"daily_data/{c}.csv", index_col=0, parse_dates=True)
    px[c] = df["close"]
P = pd.DataFrame(px).dropna()
RET = P.pct_change()

# BTC 약세 게이트
btc = P["KRW-BTC"]; ma = btc.rolling(200).mean(); slope = ma.diff(20)
bear = (btc < ma) & (slope < 0)

def run(lookback, topk, rebal, gate):
    """top-k 동일가중, rebal일마다 재조정. gate=True면 약세장 현금."""
    dates = P.index
    eq = 1.0; held = []; curve = [(dates[200], 1.0)]
    last_rb = -10**9
    for i in range(200, len(dates)):
        d = dates[i]
        # 일일 수익 적용(보유분)
        if held:
            r = RET.iloc[i][held].mean()
            eq *= (1 + r)
        # 재조정일?
        if i - last_rb >= rebal:
            mom = P.iloc[i-lookback:i+1].pct_change(lookback).iloc[-1]  # lookback 누적수익
            if gate and bear.iloc[i]:
                new = []
            else:
                new = list(mom.sort_values(ascending=False).head(topk).index)
            # 회전비용: 바뀐 종목 수만큼 왕복비용 부과
            turn = len(set(new) ^ set(held))
            if turn:
                cost = turn/ max(len(new) if new else 1,1) * (FEE + np.mean(list(SLIP.values())))
                eq *= (1 - cost)
            held = new; last_rb = i
        curve.append((d, eq))
    cv = pd.Series(dict(curve))
    yrs = (cv.index[-1]-cv.index[0]).days/365.25
    cagr = cv.iloc[-1]**(1/yrs)-1
    mdd = (cv/cv.cummax()-1).min()
    return cagr, mdd, cv.iloc[-1]

# 벤치마크: 동일가중 전부 보유 / BTC 보유
def bench(series_or_eqw):
    if series_or_eqw == "eqw":
        cv = (1+RET.iloc[200:]).cumprod().mean(axis=1)
    else:
        cv = P[series_or_eqw].iloc[200:]/P[series_or_eqw].iloc[200]
    yrs=(cv.index[-1]-cv.index[0]).days/365.25
    return cv.iloc[-1]**(1/yrs)-1, (cv/cv.cummax()-1).min()

print("=== 벤치마크 (3년 일봉) ===")
bc,bm = bench("KRW-BTC"); print(f"  BTC 보유      CAGR {bc*100:+.1f}%  MDD {bm*100:.0f}%")
ec,em = bench("eqw");     print(f"  동일가중10    CAGR {ec*100:+.1f}%  MDD {em*100:.0f}%")
print("\n=== 교차 모멘텀 로테이션 (비용 차감) ===")
print(f"{'lookback':>9}{'topk':>5}{'rebal':>6}{'gate':>6}{'CAGR':>9}{'MDD':>7}")
best=None
for lb in (14,30,60,90):
    for k in (1,2,3):
        for rb in (3,7,14):
            for g in (True,False):
                cagr,mdd,fin = run(lb,k,rb,g)
                tag="bear현금" if g else "상시"
                print(f"{lb:>9}{k:>5}{rb:>6}{tag:>8}{cagr*100:>8.1f}%{mdd*100:>6.0f}%")
                # 알파 기준: 동일가중 CAGR 초과 + MDD 동일가중보다 얕음
                score = (cagr-ec) - max(0,(abs(mdd)-abs(em)))
                if best is None or score>best[0]: best=(score,lb,k,rb,g,cagr,mdd)
print(f"\n>>> 최고(알파=CAGR-동일가중, MDD패널티): lookback={best[1]} topk={best[2]} rebal={best[3]} "
      f"gate={'bear현금' if best[4] else '상시'} → CAGR {best[5]*100:+.1f}% MDD {best[6]*100:.0f}%")
print(f"    동일가중 대비 알파 {(best[5]-ec)*100:+.1f}%p")
