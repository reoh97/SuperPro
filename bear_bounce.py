"""약세장 반등(bear-bounce) 엣지 — 빈 국면(약세 20%) 사냥.
가설: 약세장에도 과매도 반등(dead-cat)은 잦다(약세날의 48%가 +).
   폭락엣지(-20% 패닉)보다 약하고 잦은 과매도를, 빠른 익절+칼손절로 먹는다.
v3와 차이: BTC 약세(MA200아래+하락) 게이트에서만 작동. 칼받기라 빠른 청산.
검증: 연속 15분봉, 약세구간만, 전/후반 OOS. 메이커. 안 되면 닫는다(현금이 정답)."""
from __future__ import annotations
import sys, numpy as np, pandas as pd, yaml, itertools
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
MAKER=0.0005
cfg=yaml.safe_load(open("config.yaml"))
COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
SLIP={c:cfg["slippage"]["by_coin"].get(c,cfg["slippage"]["default_pct"]) for c in COINS}

# BTC 약세 게이트(일봉) → 날짜 집합
btc=pd.read_csv("daily_data/KRW-BTC.csv",index_col=0,parse_dates=True)
c=btc["close"]; ma=c.rolling(200).mean(); slope=ma.diff(20)
bear=(c<ma)&(slope<0)
BEAR_DAYS=set(d.date() for d,x in bear.items() if x)

def prep(tk):
    d=pd.read_csv(f"min15_data/{tk}.csv",index_col=0,parse_dates=True); n=20
    d["sma"]=d["close"].rolling(n).mean(); d["std"]=d["close"].rolling(n).std()
    return d.dropna()

DATA={tk:prep(tk) for tk in COINS}

def run(tk,k,tp,stop,maxhold):
    d=DATA[tk]; c=d["close"].to_numpy(); lo=d["low"].to_numpy(); hi=d["high"].to_numpy()
    sma=d["sma"].to_numpy(); std=d["std"].to_numpy(); idx=d.index
    s=SLIP[tk]; pos=None; rets=[]
    for i in range(len(d)):
        in_bear = idx[i].date() in BEAR_DAYS
        lower=sma[i]-k*std[i]
        if pos is None:
            if in_bear and lo[i]<=lower:                       # 약세 + 과매도 → 지정가 매수
                pos={"entry":lower*(1+MAKER),"i":i,"tp":lower*(1+tp)}
        else:
            ex=None; maker=True
            if hi[i]>=pos["tp"]: ex=pos["tp"]                  # 빠른 반등 익절(메이커)
            elif c[i]<=pos["entry"]*(1-stop): ex=c[i]*(1-s); maker=False  # 칼손절
            elif i-pos["i"]>=maxhold: ex=c[i]*(1-s); maker=False         # 시간초과
            if ex is not None:
                fee=MAKER if maker else 0
                rets.append((idx[i],ex*(1-fee)/pos["entry"]-1)); pos=None
    return rets

def summ(rets):
    if not rets: return 0,0,0,0
    r=np.array([x[1] for x in rets]); eq=np.cumprod(1+r)
    return len(r), r.sum()*100, (r>0).mean()*100, ((eq-np.maximum.accumulate(eq))/np.maximum.accumulate(eq)).min()*100

print("="*70); print("  약세장 반등 엣지 스윕 (약세구간만, 전코인 연속, 메이커)"); print("="*70)
print(f"{'k':>4}{'tp':>6}{'stop':>6}{'hold':>5}{'거래':>6}{'총수익%':>9}{'승률%':>7}{'MDD%':>7}{'+코인':>6}")
best=None
for k,tp,stop,mh in itertools.product([1.5,2.0,2.5],[0.02,0.03,0.05],[0.03,0.05],[10,20]):
    tot=[]; pc=0
    for tk in COINS:
        rr=run(tk,k,tp,stop,mh); _,sm,_,_=summ(rr); tot+=rr
        if sm>0: pc+=1
    n,sm,wr,md=summ(sorted(tot))
    print(f"{k:>4}{tp:>6}{stop:>6}{mh:>5}{n:>6}{sm:>9.1f}{wr:>7.0f}{md:>7.0f}{pc:>5}/10")
    if best is None or sm>best[0]: best=(sm,k,tp,stop,mh)
print(f"\n>>> 최고: k={best[1]} tp={best[2]} stop={best[3]} hold={best[4]} (총수익 {best[0]:+.0f}%)")
allr=[]
for tk in COINS: allr+=run(tk,best[1],best[2],best[3],best[4])
allr.sort(); mid=allr[len(allr)//2][0] if allr else None
print("=== 아웃오브샘플 ===")
for nm,rr in [("전반",[x for x in allr if x[0]<=mid]),("후반",[x for x in allr if x[0]>mid])]:
    n,sm,wr,md=summ(rr); print(f"  {nm}: 거래 {n} 총수익 {sm:+.1f}% 승률 {wr:.0f}% MDD {md:.0f}%")
