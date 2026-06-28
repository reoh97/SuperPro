"""펀딩 확증 코어 검증(옵션A) — 코어 돌파진입에 '펀딩 동의' 게이트.
가설: 펀딩(포지셔닝)이 추세에 동의(고펀딩)할 때만 진입하면 코어 품질↑.
v4 교훈(필터가 해칠 수 있음) 유념 — 베이스라인 대비 CAGR/MDD 개선 + OOS 유지라야 채택.
펀딩=무상관(+0.09)이라 코어 가격신호에 '다른 축' 확증 추가 가능성."""
from __future__ import annotations
import sys, glob, os, numpy as np, pandas as pd, yaml
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
FEE=0.0005
cfg=yaml.safe_load(open("config.yaml")); lt=cfg["longterm"]
SLIP={c:cfg["slippage"]["by_coin"].get(c,cfg["slippage"]["default_pct"]) for c in
      ["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]}
MAJORS=lt["tickers"]
ALL=list(SLIP)

def _atr(df,n=14):
    tr=pd.concat([(df["high"]-df["low"]),(df["high"]-df["close"].shift()).abs(),
                  (df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()

def fund_z(tk):
    raw=pd.read_csv(f"funding_data/{tk}.csv",index_col=0)
    raw.index=pd.to_datetime(raw.index,utc=True,format="ISO8601").tz_localize(None)
    fd=raw["funding_rate"].resample("1D").mean()
    z=(fd-fd.rolling(30).mean())/fd.rolling(30).std()
    z.index=z.index.normalize(); return z

def core_curve(df, fz=None, gate=None, slip=0.0):
    """gate=None: 베이스라인. gate=값: 진입시 펀딩z>=gate 요구."""
    en,ex,ak,mn=lt["entry_n"],lt["exit_n"],lt["atr_k"],lt["ma_n"]
    c=df["close"].to_numpy(float); h=df["high"].to_numpy(float)
    ma=df["close"].rolling(mn).mean().to_numpy()
    dhi=df["high"].rolling(en).max().shift(1).to_numpy()
    dlo=df["low"].rolling(ex).min().shift(1).to_numpy()
    a=_atr(df).to_numpy(); idx=df.index
    z=fz.reindex(idx.normalize()).to_numpy() if fz is not None else None
    eq=1.0; pos=None; out_i,out_v=[],[]
    for i in range(mn+1,len(c)):
        if pos is not None:
            pos["peak"]=max(pos["peak"],h[i]); stop=pos["peak"]-ak*a[i]
            if c[i]<=dlo[i] or c[i]<=stop:
                eq*=(c[i]*(1-slip)/pos["entry"])*(1-FEE); pos=None
        elif c[i]>ma[i] and c[i]>=dhi[i]:
            ok = gate is None or (z is not None and not np.isnan(z[i]) and z[i]>=gate)
            if ok: pos={"entry":c[i]*(1+FEE)*(1+slip),"peak":h[i]}
        out_i.append(idx[i]); out_v.append(eq*(c[i]/pos["entry"]) if pos else eq)
    return pd.Series(out_v,index=pd.DatetimeIndex(out_i))

def stats(s):
    yrs=(s.index[-1]-s.index[0]).days/365.25
    return (s.iloc[-1]**(1/yrs)-1), ((s/s.cummax()-1).min())

def portfolio(gate, coins):
    curves=[]
    for tk in coins:
        df=pd.read_csv(f"daily_data/{tk}.csv",index_col=0,parse_dates=True)
        curves.append(core_curve(df, fund_z(tk), gate, SLIP[tk]))
    mat=pd.concat(curves,axis=1).sort_index().ffill().dropna()
    port=(1+mat.pct_change().mean(axis=1)).cumprod()
    return port

print("="*66); print("  펀딩 확증 코어 (메이저4) — 베이스라인 vs 펀딩게이트"); print("="*66)
print(f"{'게이트':>12}{'CAGR':>9}{'MDD':>8}{'전반CAGR':>11}{'후반CAGR':>11}")
for gate in [None,0.0,0.5,1.0]:
    p=portfolio(gate,MAJORS)
    cagr,mdd=stats(p)
    mid=p.index[len(p)//2]
    c1,_=stats(p[p.index<=mid]); c2,_=stats(p[p.index>mid])
    tag="없음(기본)" if gate is None else f"펀딩z≥{gate}"
    print(f"{tag:>12}{cagr*100:>8.1f}%{mdd*100:>7.0f}%{c1*100:>10.1f}%{c2*100:>10.1f}%")
print("\n  전코인10 적용:")
print(f"{'게이트':>12}{'CAGR':>9}{'MDD':>8}{'전반CAGR':>11}{'후반CAGR':>11}")
for gate in [None,0.0,0.5]:
    p=portfolio(gate,ALL); cagr,mdd=stats(p); mid=p.index[len(p)//2]
    c1,_=stats(p[p.index<=mid]); c2,_=stats(p[p.index>mid])
    tag="없음(기본)" if gate is None else f"펀딩z≥{gate}"
    print(f"{tag:>12}{cagr*100:>8.1f}%{mdd*100:>7.0f}%{c1*100:>10.1f}%{c2*100:>10.1f}%")
