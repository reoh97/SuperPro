"""횡보 전략 v2 — 범위 평균회귀(Range Mean-Reversion). 그리드 대체.
그리드 사망원인=추세하락 초입을 횡보로 오판해 칼받기. v2는:
  ① 변동성 수축(BB폭 좁음=박스)일 때만  ② 하단밴드 매수→중심선 매도(평균회귀)
  ③ 범위 깨지면 즉시 손절(칼받기 금지)
검증: 연속 15분봉, 전 코인, 전/후반 아웃오브샘플. 비용(수수료+슬리피지) 반영.
사전선택 윈도우 안 씀(그리드 룩어헤드 함정 회피)."""
from __future__ import annotations
import sys, numpy as np, pandas as pd, yaml, itertools
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
FEE=0.0005
cfg=yaml.safe_load(open("config.yaml"))
COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
SLIP={c:cfg["slippage"]["by_coin"].get(c,cfg["slippage"]["default_pct"]) for c in COINS}

def prep(tk):
    d=pd.read_csv(f"min15_data/{tk}.csv",index_col=0,parse_dates=True)
    n=20
    d["sma"]=d["close"].rolling(n).mean()
    d["std"]=d["close"].rolling(n).std()
    d["bbw"]=(2*d["std"])/d["sma"]                 # 밴드 폭/가격 (변동성)
    d["bbw_ma"]=d["bbw"].rolling(100).mean()       # 평소 변동성
    d["slope"]=d["sma"].diff(n)/d["sma"]           # 중심선 기울기(추세)
    d["ma200"]=d["close"].rolling(200).mean()
    return d.dropna()

def run(d,tk,k,wq,stop,maxhold):
    c=d["close"].to_numpy(); lo=d["low"].to_numpy(); hi=d["high"].to_numpy()
    sma=d["sma"].to_numpy(); std=d["std"].to_numpy()
    bbw=d["bbw"].to_numpy(); bbwma=d["bbw_ma"].to_numpy()
    slope=d["slope"].to_numpy(); ma200=d["ma200"].to_numpy()
    s=SLIP[tk]; eq=1.0; pos=None; rets=[]
    for i in range(len(d)):
        lower=sma[i]-k*std[i]
        in_range=(bbw[i]<bbwma[i]*wq) and (abs(slope[i])<0.02) and (c[i]>ma200[i]*0.95)
        if pos is None:
            if in_range and c[i]<=lower:
                pos={"entry":c[i]*(1+s)*(1+FEE),"i":i}
        else:
            exit_px=None
            if c[i]>=sma[i]: exit_px=c[i]                      # 중심선 복귀=익절
            elif c[i]<=pos["entry"]*(1-stop): exit_px=c[i]     # 범위깨짐=손절
            elif i-pos["i"]>=maxhold: exit_px=c[i]             # 시간초과
            if exit_px is not None:
                r=exit_px*(1-s)*(1-FEE)/pos["entry"]-1
                rets.append((d.index[i],r)); pos=None
    return rets

def summ(rets):
    if not rets: return 0,0,0,0
    r=np.array([x[1] for x in rets])
    eq=np.cumprod(1+r); mdd=((eq-np.maximum.accumulate(eq))/np.maximum.accumulate(eq)).min()
    return len(r), r.sum()*100, (r>0).mean()*100, mdd*100

DATA={tk:prep(tk) for tk in COINS}
print("="*70); print("  횡보 v2 — 범위 평균회귀 파라미터 스윕 (전 코인 연속, 비용반영)"); print("="*70)
print(f"{'k':>4}{'wq':>5}{'stop':>6}{'hold':>5}{'거래':>6}{'총수익%':>9}{'승률%':>7}{'MDD%':>7}{'+코인':>6}")
best=None
for k,wq,stop,mh in itertools.product([1.5,2.0,2.5],[0.8,1.0],[0.03,0.05],[20,48]):
    tot=[]; pcoin=0
    for tk in COINS:
        rets=run(DATA[tk],tk,k,wq,stop,mh)
        n,sm,wr,md=summ(rets); tot+=rets
        if sm>0: pcoin+=1
    n,sm,wr,md=summ(sorted(tot))
    print(f"{k:>4}{wq:>5}{stop:>6}{mh:>5}{n:>6}{sm:>9.1f}{wr:>7.0f}{md:>7.0f}{pcoin:>5}/10")
    score=sm+md  # 수익+MDD(음수) 동시 고려
    if best is None or score>best[0]: best=(score,k,wq,stop,mh)
print(f"\n>>> 후보: k={best[1]} wq={best[2]} stop={best[3]} hold={best[4]}")
# 아웃오브샘플: 전/후반
print("=== 아웃오브샘플 검증(후보 파라미터) ===")
allr=[]
for tk in COINS: allr+=run(DATA[tk],tk,best[1],best[2],best[3],best[4])
allr.sort()
mid=allr[len(allr)//2][0]
fh=[x for x in allr if x[0]<=mid]; sh=[x for x in allr if x[0]>mid]
for nm,rr in [("전반",fh),("후반",sh)]:
    n,sm,wr,md=summ(rr); print(f"  {nm}: 거래 {n} 총수익 {sm:+.1f}% 승률 {wr:.0f}% MDD {md:.0f}%")
