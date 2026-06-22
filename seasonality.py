"""시간대/요일 계절성 엣지 — 불장 베타가 아닌 미시구조 축.
'특정 시간대(KST)가 체계적으로 더 오르나?' 추세와 완전히 다른 축.
핵심 회의 테스트: 전/후반 반갈라 패턴이 지속되나(아웃오브샘플). 안 되면 닫는다.
데이터: min15_data/ 10코인 15분봉(타임스탬프 KST)."""
from __future__ import annotations
import sys, numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

COINS=["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]

# 전 코인 15분봉 → 시간대별 다음봉 수익 집계
frames=[]
for c in COINS:
    df=pd.read_csv(f"min15_data/{c}.csv",index_col=0,parse_dates=True)
    r=df["close"].pct_change().shift(-1)   # 이번 봉 종가→다음 봉 종가 수익(이번봉 끝에 진입한다 가정)
    g=pd.DataFrame({"ret":r,"hour":df.index.hour,"dow":df.index.dayofweek,"coin":c,"t":df.index})
    frames.append(g.dropna())
A=pd.concat(frames,ignore_index=True)
mid=A["t"].quantile(0.5)

def tbl(sub,key):
    return sub.groupby(key)["ret"].agg(["mean","count"])

print("=== 시간대(KST)별 평균 다음봉 수익 (전 코인, bp=0.01%) ===")
print(f"{'시':>3}{'전체bp':>9}{'전반bp':>9}{'후반bp':>9}{'승률%':>7}")
allh=tbl(A,"hour"); fh=tbl(A[A.t<=mid],"hour"); sh=tbl(A[A.t>mid],"hour")
wr=A.groupby("hour")["ret"].apply(lambda x:(x>0).mean()*100)
for h in range(24):
    a=allh["mean"].get(h,0)*1e4; f=fh["mean"].get(h,0)*1e4; s=sh["mean"].get(h,0)*1e4
    print(f"{h:>3}{a:>9.2f}{f:>9.2f}{s:>9.2f}{wr.get(h,0):>7.1f}")

print("\n=== 요일별 (0=월~6=일) ===")
alld=tbl(A,"dow"); fd=tbl(A[A.t<=mid],"dow"); sd=tbl(A[A.t>mid],"dow")
print(f"{'요일':>4}{'전체bp':>9}{'전반bp':>9}{'후반bp':>9}")
for d in range(7):
    print(f"{d:>4}{alld['mean'].get(d,0)*1e4:>9.2f}{fd['mean'].get(d,0)*1e4:>9.2f}{sd['mean'].get(d,0)*1e4:>9.2f}")

# 아웃오브샘플 일관성: 전반 상위시간대가 후반에도 +인가
fmean=fh["mean"]; smean=sh["mean"]
top_f=fmean.sort_values(ascending=False).head(6).index
print(f"\n=== 아웃오브샘플 일관성 ===")
print(f"  전반 상위 6시간대: {sorted(top_f.tolist())}")
print(f"  그 시간대 후반 평균수익: {smean.loc[top_f].mean()*1e4:+.2f}bp (전체평균 {smean.mean()*1e4:+.2f}bp)")
rf=fmean.rank(); rs=smean.rank()
print(f"  전·후반 시간대순위 상관: {np.corrcoef(rf,rs)[0,1]:+.2f}")
print(f"  최고시간대 신호크기 ~{allh['mean'].abs().max()*1e4:.1f}bp  vs  왕복비용 ~10bp+ → 거래불가")
