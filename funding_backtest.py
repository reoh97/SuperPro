"""펀딩비 엣지 검증 — funding_data 받으면 실행. 포지셔닝이 미래수익 예측하나.
가설: 펀딩 극단 음수(숏 과밀) → 스퀴즈로 튐(롱 신호). 극단 양수(롱 과열) → 조정.
검증(과최적화 차단): ①IC train/test ②극단음수 진입 롱전략 OOS ③코어/v3 상관.
롱온리라 '음수 펀딩 매수'만 거래 가능(양수→하락은 공매도 필요라 패스)."""
from __future__ import annotations
import sys, os, glob, numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

FUND="funding_data"
if not glob.glob(f"{FUND}/KRW-*.csv"):
    print("funding_data 없음 — 집에서 fetch_funding.py 실행 후 push 하세요."); sys.exit(0)
COINS=[os.path.basename(p)[:-4] for p in sorted(glob.glob(f"{FUND}/KRW-*.csv"))]
ZWIN=30  # 인과적 z-score 창(일, 펀딩 일일화 기준)

def load(tk):
    f=pd.read_csv(f"{FUND}/{tk}.csv",index_col=0,parse_dates=True)["funding_rate"]
    fd=f.resample("1D").mean().dropna()                 # 일일 펀딩(3회 평균)
    p=pd.read_csv(f"daily_data/{tk}.csv",index_col=0,parse_dates=True)["close"]
    p.index=p.index.normalize(); fd.index=fd.index.normalize()
    df=pd.DataFrame({"fund":fd,"close":p}).dropna()
    df["z"]=(df["fund"]-df["fund"].rolling(ZWIN).mean())/df["fund"].rolling(ZWIN).std()
    return df.dropna()

# ── ① IC: 펀딩 z vs 미래수익(1/3/7일) ──
print("="*60); print("  펀딩비 엣지 검증"); print("="*60)
rows=[]
for tk in COINS:
    d=load(tk)
    for H in (1,3,7):
        d[f"fwd{H}"]=d["close"].shift(-H)/d["close"]-1
    d["coin"]=tk; rows.append(d)
A=pd.concat(rows).dropna(subset=["z","fwd1","fwd3","fwd7"]).sort_index()
mid=A.index[len(A)//2]
tr=A[A.index<=mid]; te=A[A.index>mid]
print("  IC (펀딩z vs 미래수익) — 음수면 '고펀딩→하락/저펀딩→상승'(가설대로)")
print(f"  {'지평':>6}{'훈련IC':>9}{'검증IC':>9}{'부호유지':>9}")
for H in (1,3,7):
    a=tr["z"].corr(tr[f"fwd{H}"]); b=te["z"].corr(te[f"fwd{H}"])
    keep="✅" if (a>0)==(b>0) and abs(b)>0.02 else "✗"
    print(f"  {H:>4}일{a*100:>8.2f}{b*100:>8.2f}{keep:>8}")

# ── ② 롱전략: 펀딩 극단음수(z<-thr) 진입, H일 보유 ──
print("\n  극단 음수펀딩 매수 → H일 보유 (전코인 풀, 비용 0.1% 차감)")
print(f"  {'z<':>6}{'H':>4}{'거래':>7}{'평균수익%':>10}{'승률%':>8}{'전반%':>8}{'후반%':>8}")
for thr in (1.0,1.5,2.0):
    for H in (3,7):
        sig=A[A["z"]<-thr].copy()
        if len(sig)<20: continue
        r=sig[f"fwd{H}"]-0.001
        f=sig[sig.index<=mid][f"fwd{H}"]-0.001; s=sig[sig.index>mid][f"fwd{H}"]-0.001
        print(f"  {-thr:>5.1f}{H:>4}{len(sig):>7}{r.mean()*100:>9.2f}{(r>0).mean()*100:>8.0f}"
              f"{f.mean()*100:>8.2f}{s.mean()*100:>8.2f}")

# ── ③ 코어/BTC 상관 (펀딩전략 일수익 vs 시장) ──
btc=pd.read_csv("daily_data/KRW-BTC.csv",index_col=0,parse_dates=True)["close"]
btc.index=btc.index.normalize(); bret=btc.pct_change()
# 펀딩전략 일별 수익 근사: 그날 진입신호 코인들의 다음날 평균수익
A2=A.copy(); A2["sig"]=(A2["z"]<-1.5).astype(int)
daily_sig=A2.groupby(A2.index).apply(lambda g: (g["sig"]*g["fwd1"]).sum()/max(g["sig"].sum(),1))
j=pd.concat([daily_sig,bret],axis=1).dropna(); j.columns=["fund","btc"]
print(f"\n  펀딩전략 일수익 vs BTC 상관: {j['fund'].corr(j['btc']):+.2f} (낮으면 무상관 보너스)")
print("="*60)
print("  판정: IC 부호유지 + 극단음수 평균수익이 비용(0.1%) 넘고 전/후반 양수 → 엣지")
