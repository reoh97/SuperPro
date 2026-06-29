"""검증된 v3(범위 평균회귀)를 알트 유니버스(266종목)에 적용.
'알트는 변동성 크다'를 추격(천장매수)이 아닌 평균회귀(페이드)로 이긴다.
작은알트 호가 얇음 → 메이커 체결 불확실 → 슬리피지 민감도 검증.
기간 6주로 짧음 → 연율화 대신 기간수익/승률/OOS. 생존편향=낙관상한."""
from __future__ import annotations
import sys, glob, os, numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
FEE=0.0005
FILES=sorted(glob.glob("alt_data/KRW-*.csv"))
MAJ={"KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"}

def prep(f):
    d=pd.read_csv(f,index_col=0,parse_dates=True)
    if len(d)<400: return None
    n=20
    d["sma"]=d["close"].rolling(n).mean(); d["std"]=d["close"].rolling(n).std()
    d["bbw"]=(2*d["std"])/d["sma"]; d["bbw_ma"]=d["bbw"].rolling(100).mean()
    d["slope"]=d["sma"].diff(n)/d["sma"]; d["ma200"]=d["close"].rolling(200).mean()
    return d.dropna()

def run(d,k,wq,stop,maxhold,slip):
    c=d["close"].to_numpy();lo=d["low"].to_numpy();hi=d["high"].to_numpy()
    sma=d["sma"].to_numpy();std=d["std"].to_numpy();bbw=d["bbw"].to_numpy()
    bbwma=d["bbw_ma"].to_numpy();slope=d["slope"].to_numpy();ma200=d["ma200"].to_numpy()
    idx=d.index; pos=None; out=[]
    for i in range(len(d)):
        lower=sma[i]-k*std[i]; upper=sma[i]+k*std[i]
        inr=(bbw[i]<bbwma[i]*wq) and (abs(slope[i])<0.02) and (c[i]>ma200[i]*0.95)
        if pos is None:
            if inr and lo[i]<=lower and c[i]>lower*(1-stop):
                pos={"entry":lower*(1+slip),"i":i,"up":upper}
        else:
            ex=None
            if hi[i]>=pos["up"]: ex=pos["up"]*(1-slip)
            elif c[i]<=pos["entry"]*(1-stop): ex=c[i]*(1-slip)
            elif i-pos["i"]>=maxhold: ex=c[i]*(1-slip)
            if ex is not None:
                out.append((idx[i], ex*(1-FEE)/(pos["entry"]*(1+FEE))-1)); pos=None
    return out

def summ(arr):
    if not arr: return None
    a=np.array([x[1] for x in arr]); w=a[a>0]; l=a[a<0]
    pf=w.sum()/-l.sum() if len(l) else 9.9
    return len(a),a.mean()*100,(a>0).mean()*100,pf,a.sum()*100

DATA=[(os.path.basename(f)[:-4],prep(f)) for f in FILES]
DATA=[(tk,d) for tk,d in DATA if d is not None]
alts=[(tk,d) for tk,d in DATA if tk not in MAJ]
print("="*72)
print(f"  v3 평균회귀 → 알트 유니버스 ({len(alts)}종목, 메이저 제외)  생존편향=낙관상한")
print("="*72)
print(f"  {'슬립':>5}{'거래':>7}{'평균%':>8}{'승률%':>7}{'PF':>6}{'총%':>8}{'전반평균':>9}{'후반평균':>9}")
for slip in (0.0005,0.003,0.006,0.010):
    allt=[]
    for tk,d in alts: allt+=run(d,2.5,0.8,0.05,20,slip)
    if not allt: continue
    allt.sort(); mid=allt[len(allt)//2][0]
    n,avg,wr,pf,tot=summ(allt)
    f1=summ([x for x in allt if x[0]<=mid]); f2=summ([x for x in allt if x[0]>mid])
    mark="★" if avg>0 and pf>1.3 else ("✗" if avg<0 else "·")
    print(f"  {slip*100:>4.2f}%{n:>7}{avg:>8.3f}{wr:>7.0f}{pf:>6.2f}{tot:>8.0f}{f1[1]:>9.3f}{f2[1]:>9.3f} {mark}")
# 비교: 같은 기간 메이저10에 v3
print(f"\n  (대조) 같은 코드로 메이저10:")
maj=[(tk,d) for tk,d in DATA if tk in MAJ]
for slip in (0.0005,0.006):
    allt=[]
    for tk,d in maj: allt+=run(d,2.5,0.8,0.05,20,slip)
    if allt:
        n,avg,wr,pf,tot=summ(allt)
        print(f"  {slip*100:>4.2f}%{n:>7}{avg:>8.3f}{wr:>7.0f}{pf:>6.2f}{tot:>8.0f}")
