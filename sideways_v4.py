"""횡보 v4 — v3에 '합의 과매도 필터' 추가. 신호마이닝이 OOS검증한 평균회귀 지표
(rsi/stoch/willr/cci/dist20)가 -2.5σ 진입에 동의할 때만 매수 → 더 센 꼬리신호.
v3 베이스라인 vs 필터(여러 임계) 비교: CAGR/MDD/승률/거래수. 좋아지면 채택."""
from __future__ import annotations
import sys, numpy as np, pandas as pd, yaml
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
MAKER=0.0005; ZWIN=480
cfg=yaml.safe_load(open("config.yaml"))
COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
SLIP=np.array([cfg["slippage"]["by_coin"].get(c,cfg["slippage"]["default_pct"]) for c in COINS])

def rsi(c,n=14):
    d=c.diff(); up=d.clip(lower=0).rolling(n).mean(); dn=(-d.clip(upper=0)).rolling(n).mean()
    return 100-100/(1+up/dn.replace(0,np.nan))
def stoch(df,n=14):
    ll=df["low"].rolling(n).min(); hh=df["high"].rolling(n).max()
    return 100*(df["close"]-ll)/(hh-ll).replace(0,np.nan)
def willr(df,n=14):
    hh=df["high"].rolling(n).max(); ll=df["low"].rolling(n).min()
    return -100*(hh-df["close"])/(hh-ll).replace(0,np.nan)
def cci(df,n=20):
    tp=(df["high"]+df["low"]+df["close"])/3; sma=tp.rolling(n).mean()
    md=(tp-sma).abs().rolling(n).mean(); return (tp-sma)/(0.015*md.replace(0,np.nan))

def prep(tk):
    d=pd.read_csv(f"min15_data/{tk}.csv",index_col=0,parse_dates=True); n=20
    d["sma"]=d["close"].rolling(n).mean(); d["std"]=d["close"].rolling(n).std()
    d["bbw"]=(2*d["std"])/d["sma"]; d["bbw_ma"]=d["bbw"].rolling(100).mean()
    d["slope"]=d["sma"].diff(n)/d["sma"]; d["ma200"]=d["close"].rolling(200).mean()
    # 합의 과매도 점수: 평균회귀 지표들 z-score → 과매도일수록 높게
    mr={"rsi":rsi(d["close"]),"stoch":stoch(d),"willr":willr(d),"cci":cci(d),
        "dist20":(d["close"]/d["sma"]-1)*100}
    zs=[]
    for s in mr.values():
        z=(s-s.rolling(ZWIN).mean())/s.rolling(ZWIN).std().replace(0,np.nan)
        zs.append(z.clip(-4,4))
    d["oversold"]=-pd.concat(zs,axis=1).mean(axis=1)   # 지표 낮을수록(과매도) 점수 높음
    return d.dropna()

DATA={tk:prep(tk) for tk in COINS}
allidx=pd.DatetimeIndex(sorted(set().union(*[set(d.index) for d in DATA.values()])))
T=len(allidx); C=len(COINS)
def col(name): return np.column_stack([DATA[tk][name].reindex(allidx).to_numpy(float) for tk in COINS])
CL=col("close");LO=col("low");HI=col("high");SMA=col("sma");STD=col("std")
BBW=col("bbw");BBWMA=col("bbw_ma");SLOPE=col("slope");MA200=col("ma200");OVS=col("oversold")
HAS=~np.isnan(CL)

def sim(k,wq,stop,maxhold,M,ovs_min,budget=3_000_000):
    per=budget/M; cash=budget
    held=np.zeros(C,bool);entry=np.zeros(C);qty=np.zeros(C);up=np.zeros(C);low0=np.zeros(C);bars=np.zeros(C,int)
    eq=np.empty(T); wins=0; trades=0
    for i in range(T):
        cl=CL[i];lo=LO[i];hi=HI[i];has=HAS[i]
        for j in range(C):
            if not held[j] or not has[j]: continue
            ex=None;maker=True
            if hi[j]>=up[j]: ex=up[j]
            elif cl[j]<=low0[j]*(1-stop): ex=cl[j]*(1-SLIP[j]);maker=False
            elif bars[j]>=maxhold: ex=cl[j]*(1-SLIP[j]);maker=False
            bars[j]+=1
            if ex is not None:
                ret=ex*(1-(MAKER if maker else 0))/entry[j]-1
                cash+=ex*(1-(MAKER if maker else 0))*qty[j]; held[j]=False
                trades+=1; wins+= 1 if ret>0 else 0
        nheld=held.sum()
        lower=SMA[i]-k*STD[i]
        inr=(BBW[i]<BBWMA[i]*wq)&(np.abs(SLOPE[i])<0.02)&(cl>MA200[i]*0.95)&has&(OVS[i]>=ovs_min)
        for j in range(C):
            if nheld>=M or cash<per*0.5: break
            if held[j] or not inr[j]: continue
            if lo[j]<=lower[j] and cl[j]>lower[j]*(1-stop):
                e=lower[j]*(1+MAKER);spend=min(per,cash)
                entry[j]=e;qty[j]=spend/e;up[j]=SMA[i][j]+k*STD[i][j];low0[j]=lower[j];bars[j]=0
                held[j]=True;cash-=spend;nheld+=1
        val=cash
        for j in range(C):
            if held[j]: val+=(cl[j] if has[j] else entry[j])*qty[j]
        eq[i]=val
    s=pd.Series(eq,index=allidx);yrs=(allidx[-1]-allidx[0]).days/365.25
    cagr=(s.iloc[-1]/budget)**(1/yrs)-1;mdd=((s-s.cummax())/s.cummax()).min()
    wr=wins/trades*100 if trades else 0
    return s,cagr,mdd,trades,wr

print("="*72);print("  횡보 v4 — 합의 과매도 필터 효과 (M=6, k2.5, 손절5%)");print("="*72)
print(f"{'필터(과매도≥)':>14}{'최종M':>8}{'CAGR':>9}{'MDD':>8}{'거래':>7}{'승률%':>7}")
for ov in [-9,0.0,0.5,1.0,1.5]:
    s,cagr,mdd,tr,wr=sim(2.5,0.8,0.05,20,6,ov)
    tag="없음(v3)" if ov<-1 else f"{ov}"
    print(f"{tag:>14}{s.iloc[-1]/1e6:>7.2f}M{cagr*100:>8.1f}%{mdd*100:>7.0f}%{tr:>7}{wr:>7.0f}")
