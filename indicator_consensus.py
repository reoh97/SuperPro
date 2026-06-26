"""지표 합의 신호마이닝 — 사람이 다 못 보는 수많은 지표 중 '진짜 예측력 있는 공통신호'를
데이터가 직접 찾게 한다. 죽은 confluence(임의 점수85)와 달리 방향을 내가 안 정함.

방법(과최적화 차단):
  1) ~16개 지표를 인과적(causal) z-score로 표준화
  2) 전반부(훈련): 각 지표 vs 미래수익 상관(IC) → 방향·강도를 데이터가 결정
  3) 후반부(검증): 그 방향이 유지되나(OOS IC). 합의(가중합)가 단일지표보다 나은가
  4) 합의 분위별 미래수익 단조성 + 비용 비교
데이터: min15_data 10코인. 미래수익 H봉(기본 8봉=2시간).
"""
from __future__ import annotations
import sys, numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
H=8           # 미래수익 지평(8봉=2시간)
ZWIN=480      # 인과적 z-score 창(480봉=5일)
COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]

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
    md=(tp-sma).abs().rolling(n).mean()
    return (tp-sma)/(0.015*md.replace(0,np.nan))
def mfi(df,n=14):
    tp=(df["high"]+df["low"]+df["close"])/3; mf=tp*df["volume"]
    pos=mf.where(tp>tp.shift(),0.0).rolling(n).sum()
    neg=mf.where(tp<tp.shift(),0.0).rolling(n).sum()
    return 100-100/(1+pos/neg.replace(0,np.nan))

def features(df):
    c=df["close"]; out={}
    out["rsi"]=rsi(c)
    out["stoch"]=stoch(df)
    out["willr"]=willr(df)
    out["cci"]=cci(df)
    out["mfi"]=mfi(df)
    out["roc10"]=c.pct_change(10)*100
    out["roc40"]=c.pct_change(40)*100
    ema9=c.ewm(span=9).mean(); ema21=c.ewm(span=21).mean()
    out["ema_cross"]=(ema9/ema21-1)*100
    ema12=c.ewm(span=12).mean(); ema26=c.ewm(span=26).mean()
    macd=ema12-ema26; out["macd_hist"]=macd-macd.ewm(span=9).mean()
    sma20=c.rolling(20).mean(); std20=c.rolling(20).std()
    out["bb_b"]=(c-sma20)/(2*std20).replace(0,np.nan)
    out["dist20"]=(c/sma20-1)*100
    out["dist50"]=(c/c.rolling(50).mean()-1)*100
    out["dist200"]=(c/c.rolling(200).mean()-1)*100
    out["vol_ratio"]=df["volume"]/df["volume"].rolling(20).mean()
    obv=(np.sign(c.diff())*df["volume"]).fillna(0).cumsum()
    out["obv_slope"]=obv.diff(10)/df["volume"].rolling(20).mean().replace(0,np.nan)
    F=pd.DataFrame(out,index=df.index)
    # 인과적 z-score
    Z=(F-F.rolling(ZWIN).mean())/F.rolling(ZWIN).std().replace(0,np.nan)
    return Z.clip(-4,4)

# 전 코인 특징 + 미래수익 풀링
frames=[]
for tk in COINS:
    df=pd.read_csv(f"min15_data/{tk}.csv",index_col=0,parse_dates=True)
    Z=features(df)
    fwd=df["close"].shift(-H)/df["close"]-1
    Z["fwd"]=fwd; Z["t"]=df.index
    frames.append(Z.dropna())
A=pd.concat(frames,ignore_index=True).sort_values("t").reset_index(drop=True)
FEAT=[c for c in A.columns if c not in ("fwd","t")]
mid=A["t"].quantile(0.5)
tr=A[A.t<=mid]; te=A[A.t>mid]

print("="*64); print(f"  지표 신호마이닝 — 미래 {H}봉(2시간) 수익 예측력 (IC=상관)"); print("="*64)
print(f"{'지표':>12}{'훈련IC':>9}{'검증IC':>9}{'부호유지':>9}")
ics={}
for f in FEAT:
    ic_tr=tr[f].corr(tr["fwd"]); ic_te=te[f].corr(te["fwd"])
    keep="✅" if (ic_tr>0)==(ic_te>0) and abs(ic_te)>0.005 else "✗"
    ics[f]=(ic_tr,ic_te)
    print(f"{f:>12}{ic_tr*100:>8.2f}{ic_te*100:>8.2f}{keep:>8}")

# 합의: 훈련IC 부호로 가중합(데이터가 방향 결정) → 검증에서 예측력?
w={f:np.sign(ics[f][0]) for f in FEAT if abs(ics[f][0])>0.003}
def consensus(D): return sum(D[f]*w[f] for f in w)/len(w)
te=te.copy(); te["cons"]=consensus(te); tr=tr.copy(); tr["cons"]=consensus(tr)
ic_cons_tr=tr["cons"].corr(tr["fwd"]); ic_cons_te=te["cons"].corr(te["fwd"])
best_single=max(abs(ics[f][1]) for f in FEAT)
print("="*64)
print(f"  합의신호 IC: 훈련 {ic_cons_tr*100:+.2f}  검증 {ic_cons_te*100:+.2f}  (최고단일 {best_single*100:.2f})")
print(f"  합의가 단일보다 {'나음 ✅' if abs(ic_cons_te)>best_single else '못함 ✗'}")
# 검증셋 합의 분위별 미래수익 단조성
te["q"]=pd.qcut(te["cons"],10,labels=False,duplicates="drop")
g=te.groupby("q")["fwd"].mean()*100
print("\n  검증셋 합의 10분위별 평균 미래수익(%) — 단조 우상향이면 진짜:")
print("   " + "  ".join(f"{int(q)}:{v:+.2f}" for q,v in g.items()))
print(f"  최상위10% {g.iloc[-1]:+.3f}% vs 최하위10% {g.iloc[0]:+.3f}%  (스프레드 {g.iloc[-1]-g.iloc[0]:+.3f}%p)")
print(f"  ※ 왕복비용 ~0.2% 기준 — 상위분위 수익이 이걸 넘어야 거래가치")
