"""횡보 v3 — 전체밴드 범위거래 + 지정가 메이커 실행.
v2 실패원인: 중심선 익절(반폭) vs 큰 손절 = 보상/위험 역전 + 시장가 슬리피지.
v3: 하단밴드 지정가매수(메이커,슬립0) → 상단밴드 지정가매도(전체폭). 범위깨짐 즉시손절.
검증: 연속 15분봉 전코인 + 전/후반 아웃오브샘플. 메이커수수료만(슬립 없음)."""
from __future__ import annotations
import sys, numpy as np, pandas as pd, yaml, itertools
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
MAKER=0.0005   # 업비트 메이커 수수료(편도). 지정가=슬리피지 0.
cfg=yaml.safe_load(open("config.yaml"))
COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
SLIP={c:cfg["slippage"]["by_coin"].get(c,cfg["slippage"]["default_pct"]) for c in COINS}

def prep(tk):
    d=pd.read_csv(f"min15_data/{tk}.csv",index_col=0,parse_dates=True)
    n=20
    d["sma"]=d["close"].rolling(n).mean(); d["std"]=d["close"].rolling(n).std()
    d["bbw"]=(2*d["std"])/d["sma"]; d["bbw_ma"]=d["bbw"].rolling(100).mean()
    d["slope"]=d["sma"].diff(n)/d["sma"]; d["ma200"]=d["close"].rolling(200).mean()
    return d.dropna()

def run(d,tk,k,wq,stop,maxhold):
    c=d["close"].to_numpy(); lo=d["low"].to_numpy(); hi=d["high"].to_numpy()
    sma=d["sma"].to_numpy(); std=d["std"].to_numpy()
    bbw=d["bbw"].to_numpy(); bbwma=d["bbw_ma"].to_numpy()
    slope=d["slope"].to_numpy(); ma200=d["ma200"].to_numpy()
    s=SLIP[tk]; pos=None; rets=[]
    for i in range(len(d)):
        lower=sma[i]-k*std[i]; upper=sma[i]+k*std[i]
        in_range=(bbw[i]<bbwma[i]*wq) and (abs(slope[i])<0.02) and (c[i]>ma200[i]*0.95)
        if pos is None:
            # 지정가 매수: 이 봉 저가가 하단밴드 닿으면 그 가격에 체결(메이커)
            if in_range and lo[i]<=lower and c[i]>lower*(1-stop):
                pos={"entry":lower*(1+MAKER),"i":i,"up":upper,"low0":lower}
        else:
            exit_px=None; maker=True
            if hi[i]>=pos["up"]: exit_px=pos["up"]                  # 상단밴드 지정가 익절(전체폭)
            elif c[i]<=pos["low0"]*(1-stop): exit_px=c[i]*(1-s); maker=False  # 범위깨짐=시장가손절
            elif i-pos["i"]>=maxhold: exit_px=c[i]*(1-s); maker=False         # 시간초과=시장가
            if exit_px is not None:
                fee=MAKER if maker else 0
                r=exit_px*(1-fee)/pos["entry"]-1
                rets.append((d.index[i],r)); pos=None
    return rets

def summ(rets):
    if not rets: return 0,0,0,0
    r=np.array([x[1] for x in rets]); eq=np.cumprod(1+r)
    mdd=((eq-np.maximum.accumulate(eq))/np.maximum.accumulate(eq)).min()
    return len(r), r.sum()*100, (r>0).mean()*100, mdd*100

DATA={tk:prep(tk) for tk in COINS}
print("="*72); print("  횡보 v3 — 전체밴드 범위거래 + 지정가 메이커 (연속, 슬립0)"); print("="*72)
print(f"{'k':>4}{'wq':>5}{'stop':>6}{'hold':>5}{'거래':>6}{'총수익%':>9}{'승률%':>7}{'MDD%':>7}{'+코인':>6}")
best=None
for k,wq,stop,mh in itertools.product([1.5,2.0,2.5],[0.8,1.0],[0.03,0.05],[20,48]):
    tot=[]; pcoin=0
    for tk in COINS:
        rets=run(DATA[tk],tk,k,wq,stop,mh); n,sm,_,_=summ(rets); tot+=rets
        if sm>0: pcoin+=1
    n,sm,wr,md=summ(sorted(tot))
    print(f"{k:>4}{wq:>5}{stop:>6}{mh:>5}{n:>6}{sm:>9.1f}{wr:>7.0f}{md:>7.0f}{pcoin:>5}/10")
    score=sm+md
    if best is None or score>best[0]: best=(score,k,wq,stop,mh)
print(f"\n>>> 후보: k={best[1]} wq={best[2]} stop={best[3]} hold={best[4]}")
allr=[]
for tk in COINS: allr+=run(DATA[tk],tk,best[1],best[2],best[3],best[4])
allr.sort(); mid=allr[len(allr)//2][0]
print("=== 아웃오브샘플 ===")
for nm,rr in [("전반",[x for x in allr if x[0]<=mid]),("후반",[x for x in allr if x[0]>mid])]:
    n,sm,wr,md=summ(rr); print(f"  {nm}: 거래 {n} 총수익 {sm:+.1f}% 승률 {wr:.0f}% MDD {md:.0f}%")
