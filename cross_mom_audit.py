"""교차모멘텀 정밀감사 — 진짜 엣지인가 한 코인 운빨인가.
 ①보유 코인 집중도 ②기간 반갈라 아웃오브샘플 ③코어(시계열추세)와 상관."""
from __future__ import annotations
import sys, numpy as np, pandas as pd, yaml
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
FEE=0.0005
cfg=yaml.safe_load(open("config.yaml"))
SLIP={c:cfg["slippage"]["by_coin"].get(c,cfg["slippage"]["default_pct"]) for c in COINS}
px={c:pd.read_csv(f"daily_data/{c}.csv",index_col=0,parse_dates=True)["close"] for c in COINS}
P=pd.DataFrame(px).dropna(); RET=P.pct_change()

def run(lb,k,rb,start=200,end=None,record=False):
    dates=P.index; end=end or len(dates)
    eq=1.0; held=[]; last=-10**9; curve=[(dates[start],1.0)]; hist=[]
    for i in range(start,end):
        if held: eq*=(1+RET.iloc[i][held].mean())
        if i-last>=rb:
            mom=P.iloc[i-lb:i+1].pct_change(lb).iloc[-1]
            new=list(mom.sort_values(ascending=False).head(k).index)
            turn=len(set(new)^set(held))
            if turn: eq*=(1-turn/max(len(new),1)*(FEE+np.mean(list(SLIP.values()))))
            held=new; last=i
            if record: hist.append((dates[i],list(held)))
        curve.append((dates[i],eq))
    cv=pd.Series(dict(curve)); yrs=(cv.index[-1]-cv.index[0]).days/365.25
    return cv, cv.iloc[-1]**(1/yrs)-1, (cv/cv.cummax()-1).min(), hist

lb,k,rb=90,1,7
# ① 집중도: 보유 분포
cv,cagr,mdd,hist=run(lb,k,rb,record=True)
from collections import Counter
cnt=Counter(c for _,h in hist for c in h)
print(f"=== ① 보유 집중도 (90일/top1/주간, 재조정 {len(hist)}회) ===")
for c,n in cnt.most_common():
    print(f"  {c.replace('KRW-',''):6} {n:3}회 ({n/len(hist)*100:.0f}%)")

# ② 아웃오브샘플: 전반/후반
mid=200+(len(P)-200)//2
cf,ca,ma_,_=run(lb,k,rb,start=200,end=mid)
cs,cb,mb,_=run(lb,k,rb,start=mid,end=len(P))
print(f"\n=== ② 아웃오브샘플 (기간 반갈라) ===")
print(f"  전반 {P.index[200].date()}~{P.index[mid].date()}  CAGR {ca*100:+.1f}%  MDD {ma_*100:.0f}%")
print(f"  후반 {P.index[mid].date()}~{P.index[-1].date()}  CAGR {cb*100:+.1f}%  MDD {mb*100:.0f}%")

# ③ 코어(시계열추세)와 상관 — 월수익 상관
# 코어 프록시: 각 코인 50일 돌파 보유(시계열 모멘텀) 동일가중
def ts_core():
    sig=(P>P.rolling(50).max().shift(1))  # 50일 신고가 돌파시 진입
    hold=sig.replace(False,np.nan).ffill(limit=20).fillna(0).clip(0,1)  # 대략 보유상태
    r=(RET*hold.shift(1)).mean(axis=1)
    return (1+r).cumprod()
core=ts_core()
xm=cv.pct_change().resample("ME").apply(lambda x:(1+x).prod()-1)
cm=core.pct_change().resample("ME").apply(lambda x:(1+x).prod()-1)
btcm=P["KRW-BTC"].pct_change().resample("ME").apply(lambda x:(1+x).prod()-1)
j=pd.concat([xm,cm,btcm],axis=1).dropna(); j.columns=["xmom","core","btc"]
print(f"\n=== ③ 월수익 상관 ===")
print(f"  교차모멘텀 vs 코어(시계열): {j['xmom'].corr(j['core']):+.2f}")
print(f"  교차모멘텀 vs BTC        : {j['xmom'].corr(j['btc']):+.2f}")
print(f"\n전체: CAGR {cagr*100:+.1f}% MDD {mdd*100:.0f}%")
