"""횡보 v3 포트폴리오 시뮬(고속) — 실제 자본분산으로 진짜 MDD 측정.
통합 시간축에 코인별 배열 정렬 후 정수인덱스 워크포워드. 예산 3M, 최대 M동시보유."""
from __future__ import annotations
import sys, numpy as np, pandas as pd, yaml
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
MAKER=0.0005
cfg=yaml.safe_load(open("config.yaml"))
COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
SLIP=np.array([cfg["slippage"]["by_coin"].get(c,cfg["slippage"]["default_pct"]) for c in COINS])

def prep(tk):
    d=pd.read_csv(f"min15_data/{tk}.csv",index_col=0,parse_dates=True); n=20
    d["sma"]=d["close"].rolling(n).mean(); d["std"]=d["close"].rolling(n).std()
    d["bbw"]=(2*d["std"])/d["sma"]; d["bbw_ma"]=d["bbw"].rolling(100).mean()
    d["slope"]=d["sma"].diff(n)/d["sma"]; d["ma200"]=d["close"].rolling(200).mean()
    return d.dropna()

DATA={tk:prep(tk) for tk in COINS}
allidx=pd.DatetimeIndex(sorted(set().union(*[set(d.index) for d in DATA.values()])))
T=len(allidx); C=len(COINS)
# 코인별 배열을 통합축에 정렬(없으면 NaN)
def col(name):
    return np.column_stack([DATA[tk][name].reindex(allidx).to_numpy(float) for tk in COINS])
CL=col("close"); LO=col("low"); HI=col("high"); SMA=col("sma"); STD=col("std")
BBW=col("bbw"); BBWMA=col("bbw_ma"); SLOPE=col("slope"); MA200=col("ma200")
HAS=~np.isnan(CL)

def sim(k,wq,stop,maxhold,M,budget=3_000_000):
    per=budget/M; cash=budget
    # 포지션 상태(코인별)
    held=np.zeros(C,bool); entry=np.zeros(C); qty=np.zeros(C); up=np.zeros(C); low0=np.zeros(C); bars=np.zeros(C,int)
    eq=np.empty(T)
    for i in range(T):
        cl=CL[i]; lo=LO[i]; hi=HI[i]; has=HAS[i]
        # 청산
        for j in range(C):
            if not held[j] or not has[j]: continue
            ex=None; maker=True
            if hi[j]>=up[j]: ex=up[j]
            elif cl[j]<=low0[j]*(1-stop): ex=cl[j]*(1-SLIP[j]); maker=False
            elif bars[j]>=maxhold: ex=cl[j]*(1-SLIP[j]); maker=False
            bars[j]+=1
            if ex is not None:
                cash+=ex*(1-(MAKER if maker else 0))*qty[j]; held[j]=False
        # 진입
        nheld=held.sum()
        lower=SMA[i]-k*STD[i]; upper=SMA[i]+k*STD[i]
        inr=(BBW[i]<BBWMA[i]*wq)&(np.abs(SLOPE[i])<0.02)&(cl>MA200[i]*0.95)&has
        for j in range(C):
            if nheld>=M or cash<per*0.5: break
            if held[j] or not inr[j]: continue
            if lo[j]<=lower[j] and cl[j]>lower[j]*(1-stop):
                e=lower[j]*(1+MAKER); spend=min(per,cash)
                entry[j]=e; qty[j]=spend/e; up[j]=upper[j]; low0[j]=lower[j]; bars[j]=0
                held[j]=True; cash-=spend; nheld+=1
        # 평가
        val=cash
        for j in range(C):
            if held[j]: val+=(cl[j] if has[j] else entry[j])*qty[j]
        eq[i]=val
    s=pd.Series(eq,index=allidx); yrs=(allidx[-1]-allidx[0]).days/365.25
    cagr=(s.iloc[-1]/budget)**(1/yrs)-1; mdd=((s-s.cummax())/s.cummax()).min()
    return s,cagr,mdd

print("="*66); print("  횡보 v3 포트폴리오 — 실제 자본분산 MDD (예산 3M, 전코인)"); print("="*66) if __name__=="__main__" else None
if __name__=="__main__":
  print(f"{'k':>4}{'wq':>5}{'stop':>6}{'hold':>5}{'M':>3}{'최종M':>8}{'CAGR':>9}{'MDD':>8}")
  best=None
  for M in (3,4,6):
    for k,wq,stop,mh in [(2.5,0.8,0.05,20),(2.5,1.0,0.05,20),(2.5,0.8,0.05,48),(2.5,0.8,0.03,20)]:
        s,cagr,mdd=sim(k,wq,stop,mh,M)
        print(f"{k:>4}{wq:>5}{stop:>6}{mh:>5}{M:>3}{s.iloc[-1]/1e6:>7.2f}M{cagr*100:>8.1f}%{mdd*100:>7.0f}%")
        if best is None or (cagr+mdd)>best[0]: best=(cagr+mdd,k,wq,stop,mh,M,cagr,mdd)
  print(f"\n>>> 최고(CAGR+MDD): M={best[5]} k={best[1]} stop={best[3]} hold={best[4]} → CAGR{best[6]*100:+.1f}% MDD{best[7]*100:.0f}%")

# ── 최종 관문: 아웃오브샘플(전/후반) + 코어 상관 ──
if __name__=="__main__":
  from ratio_optimize import core_curve
  print("\n"+"="*66); print("  최종 검증 — 아웃오브샘플 + 코어 상관"); print("="*66)
  s,cagr,mdd=sim(2.5,0.8,0.05,20,6)
  half=allidx[len(allidx)//2]
  s1=s[s.index<=half]; s2=s[s.index>half]
  def sub(x):
    return (x.iloc[-1]/x.iloc[0]-1)*100,((x-x.cummax())/x.cummax()).min()*100
  r1,m1=sub(s1); r2,m2=sub(s2)
  print(f"  전반: 수익 {r1:+.1f}% MDD {m1:.0f}%   |   후반: 수익 {r2:+.1f}% MDD {m2:.0f}%")
  ce=None
  for tk in cfg["longterm"]["tickers"]:
    cv=core_curve(pd.read_csv(f"daily_data/{tk}.csv",index_col=0,parse_dates=True),cfg["longterm"])
    ce=cv if ce is None else ce.add(cv,fill_value=1.0)
  sm=s.resample("ME").last().pct_change()
  cm=(ce/4).resample("ME").last().pct_change()
  btc=pd.read_csv("daily_data/KRW-BTC.csv",index_col=0,parse_dates=True)["close"]
  bm=btc.resample("ME").last().pct_change()
  j=pd.concat([sm,cm,bm],axis=1).dropna(); j.columns=["side","core","btc"]
  print(f"  월수익 상관 — vs 코어: {j['side'].corr(j['core']):+.2f}   vs BTC: {j['side'].corr(j['btc']):+.2f}")
  print(f"  전체: CAGR {cagr*100:+.1f}% MDD {mdd*100:.0f}%")
