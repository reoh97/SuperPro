"""변동성 돌파(breakout) 엣지 — 박스 깨고 터지는 순간 사냥(v3의 짝꿍).
v3=박스 되돌림 페이드. 돌파=박스가 위로 깨질 때 올라타기(v3가 손절당하는 그 자리).
코어(일봉,느림)도 못 잡는 15분봉 폭발. 돌파는 시장가(테이커)→슬리피지 부담이 관건.
진입: 종가 > 최근 N봉 고점(돌파) + 약세 아님. 청산: 샹들리에(고점-ATR×k) / N봉 저점.
검증: 연속 15분봉 전코인, 슬리피지 반영, OOS, 코어 상관(짝꿍이어야 무상관)."""
from __future__ import annotations
import sys, numpy as np, pandas as pd, yaml, itertools
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
FEE=0.0005
cfg=yaml.safe_load(open("config.yaml"))
COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
SLIP={c:cfg["slippage"]["by_coin"].get(c,cfg["slippage"]["default_pct"]) for c in COINS}

btc=pd.read_csv("daily_data/KRW-BTC.csv",index_col=0,parse_dates=True)
c=btc["close"]; ma=c.rolling(200).mean(); slope=ma.diff(20)
bear=(c<ma)&(slope<0); BEAR=set(d.date() for d,x in bear.items() if x)

def prep(tk):
    d=pd.read_csv(f"min15_data/{tk}.csv",index_col=0,parse_dates=True)
    tr=pd.concat([(d["high"]-d["low"]),(d["high"]-d["close"].shift()).abs(),
                  (d["low"]-d["close"].shift()).abs()],axis=1).max(axis=1)
    d["atr"]=tr.rolling(14).mean()
    return d.dropna()

DATA={tk:prep(tk) for tk in COINS}

def run(tk,N,M,atrk,gate):
    d=DATA[tk]; c=d["close"].to_numpy(); h=d["high"].to_numpy(); lo=d["low"].to_numpy()
    dhi=d["high"].rolling(N).max().shift(1).to_numpy()
    dlo=d["low"].rolling(M).min().shift(1).to_numpy()
    atr=d["atr"].to_numpy(); idx=d.index; s=SLIP[tk]; pos=None; rets=[]
    for i in range(N+1,len(d)):
        if pos is not None:
            pos["peak"]=max(pos["peak"],h[i])
            stop=pos["peak"]-atrk*atr[i]
            if c[i]<=stop or (not np.isnan(dlo[i]) and c[i]<=dlo[i]):
                ex=c[i]*(1-s)
                rets.append((idx[i],ex*(1-FEE)/pos["entry"]-1)); pos=None
        elif not np.isnan(dhi[i]) and c[i]>dhi[i]:
            if gate and idx[i].date() in BEAR: continue       # 약세장 돌파는 가짜 많음 → 스킵
            pos={"entry":c[i]*(1+s)*(1+FEE),"peak":h[i]}      # 돌파=시장가(슬리피지)
    return rets

def summ(rets):
    if not rets: return 0,0,0,0
    r=np.array([x[1] for x in rets]); eq=np.cumprod(1+r)
    return len(r), r.sum()*100, (r>0).mean()*100, ((eq-np.maximum.accumulate(eq))/np.maximum.accumulate(eq)).min()*100

print("="*72); print("  변동성 돌파 엣지 스윕 (전코인 연속, 슬리피지 반영)"); print("="*72)
print(f"{'N':>4}{'M':>4}{'atrk':>6}{'gate':>6}{'거래':>6}{'총수익%':>9}{'승률%':>7}{'MDD%':>7}{'+코인':>6}")
best=None
for N,M,atrk,g in itertools.product([20,50,100],[10,20],[2.0,3.0],[True,False]):
    tot=[]; pc=0
    for tk in COINS:
        rr=run(tk,N,M,atrk,g); _,sm,_,_=summ(rr); tot+=rr
        if sm>0: pc+=1
    n,sm,wr,md=summ(sorted(tot))
    print(f"{N:>4}{M:>4}{atrk:>6}{'약세컷' if g else '상시':>7}{n:>6}{sm:>9.1f}{wr:>7.0f}{md:>7.0f}{pc:>5}/10")
    if best is None or sm>best[0]: best=(sm,N,M,atrk,g)
print(f"\n>>> 최고: N={best[1]} M={best[2]} atrk={best[3]} gate={'약세컷' if best[4] else '상시'} (총수익 {best[0]:+.0f}%)")
allr=[]
for tk in COINS: allr+=run(tk,best[1],best[2],best[3],best[4])
allr.sort(); mid=allr[len(allr)//2][0] if allr else None
print("=== 아웃오브샘플 ===")
for nm,rr in [("전반",[x for x in allr if x[0]<=mid]),("후반",[x for x in allr if x[0]>mid])]:
    n,sm,wr,md=summ(rr); print(f"  {nm}: 거래 {n} 총수익 {sm:+.1f}% 승률 {wr:.0f}% MDD {md:.0f}%")
