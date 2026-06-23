"""통합 올웨더 백테스트 — 살아남은 3엔진 합산(코어+폭락+그리드) on 2,000만원.
배분: 코어 15M(메이저4×3.75M) / 그리드 3M(알트6×0.5M) / 폭락 2M(공유풀,10코인).
각 엔진을 날짜축 일봉 KRW 곡선으로 만들어 합산 → CAGR/MDD/월 인출(HWM 적립).
슬리피지·수수료 전부 반영. 데이터: daily_data(일봉)+min15_data(15분봉) 3년."""
from __future__ import annotations
import sys, os, glob, numpy as np, pandas as pd, yaml, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ratio_optimize import core_curve
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

FEE = 0.0005
cfg = yaml.safe_load(open("config.yaml"))
SLIP = {c: cfg["slippage"]["by_coin"].get(c, cfg["slippage"]["default_pct"]) for c in
        ["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]}
MAJORS = cfg["longterm"]["tickers"]                 # BTC ETH XRP SOL
ALTS = ["KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
ALL = MAJORS + ALTS

def daily(tk):  return pd.read_csv(f"daily_data/{tk}.csv", index_col=0, parse_dates=True)
def m15(tk):    return pd.read_csv(f"min15_data/{tk}.csv", index_col=0, parse_dates=True)

# ── 1) 코어(일봉 추세추종) — 메이저4, 코인당 3.75M ──
PER_CORE = cfg["longterm"]["per_coin_krw"]
core_eq = None
for tk in MAJORS:
    cv = core_curve(daily(tk), cfg["longterm"], slip=SLIP[tk]) * PER_CORE
    core_eq = cv if core_eq is None else core_eq.add(cv, fill_value=PER_CORE)

# ── 2) 폭락엣지(일봉, 2M 공유풀, max_pos2=1M씩, BTC게이트) ──
def capit_curve():
    cp = cfg["capitulation"]
    budget=cp["budget_krw"]; dp=cp["drop_pct"]; dd=cp["drop_days"]; vm=cp["vol_mult"]
    tp=cp["tp_pct"]; hold=cp["hold_days"]; maxp=cp["max_pos"]; per=budget/maxp
    dfs={tk:daily(tk) for tk in ALL}
    btc=dfs["KRW-BTC"]; ma=btc["close"].rolling(cp["gate_ma_n"]).mean()
    slope=ma.diff(cp["gate_slope_n"]); bear=(btc["close"]<ma)&(slope<0)
    dates=sorted(set().union(*[set(d.index) for d in dfs.values()]))
    cash=budget; pos=[]; out={}
    for d in dates:
        # 청산
        keep=[]
        for p in pos:
            df=dfs[p["tk"]]
            if d not in df.index: keep.append(p); continue
            hi=float(df.loc[d,"high"]); cl=float(df.loc[d,"close"]); s=SLIP[p["tk"]]
            tgt=p["entry"]*(1+tp)
            if hi>=tgt:
                cash+=tgt*(1-s)*(1-FEE)*p["sz"]; continue
            p["hold"]-=1
            if p["hold"]<=0:
                cash+=cl*(1-s)*(1-FEE)*p["sz"]; continue
            keep.append(p)
        pos=keep
        # 진입(약세 게이트)
        if not (d in bear.index and bool(bear.loc[d])):
            for tk in ALL:
                if len(pos)>=maxp or cash<per*0.5: break
                if any(p["tk"]==tk for p in pos): continue
                df=dfs[tk]
                if d not in df.index: continue
                i=df.index.get_loc(d)
                if i<dd+21: continue
                ret=df["close"].iloc[i]/df["close"].iloc[i-dd]-1
                vma=df["volume"].iloc[i-21:i].mean(); vnow=df["volume"].iloc[i]
                if ret<=-dp and vnow>vma*vm:
                    s=SLIP[tk]; entry=float(df["close"].iloc[i])*(1+s)*(1+FEE)
                    spend=min(per,cash); sz=spend/entry
                    pos.append({"tk":tk,"entry":entry,"sz":sz,"hold":hold}); cash-=spend
        # 평가
        val=cash
        for p in pos:
            df=dfs[p["tk"]]
            cl=float(df.loc[d,"close"]) if d in df.index else p["entry"]
            val+=cl*p["sz"]
        out[d]=val
    return pd.Series(out)
capit_eq=capit_curve()

# ── 3) 그리드 = 사망(연속데이터 평균 -13%, MDD-60%, 룩어헤드 편향). 3M은 현금버퍼. ──
CASH_BUF = 3_000_000

# ── 합산 (코어 + 폭락 + 현금버퍼) ──
full=pd.date_range(min(core_eq.index.min(),capit_eq.index.min()),
                   max(core_eq.index.max(),capit_eq.index.max()),freq="D")
ce=core_eq.reindex(full).ffill().fillna(PER_CORE*len(MAJORS))
pe=capit_eq.reindex(full).ffill().fillna(cfg["capitulation"]["budget_krw"])
ge=pd.Series(CASH_BUF, index=full)              # 현금버퍼(놀리는 3M)
total=ce+pe+ge
# 코어 워밍업(200일) 끝나는 시점부터 평가
start=core_eq.index.min()
total=total[total.index>=start]; ce=ce[ce.index>=start]; pe=pe[pe.index>=start]; ge=ge[ge.index>=start]

def stats(s):
    yrs=(s.index[-1]-s.index[0]).days/365.25
    cagr=(s.iloc[-1]/s.iloc[0])**(1/yrs)-1
    mdd=((s-s.cummax())/s.cummax()).min()
    return cagr,mdd,yrs

cagr,mdd,yrs=stats(total)
init=total.iloc[0]
print("="*64)
print(f"  통합 올웨더 백테스트  ({start.date()} ~ {total.index[-1].date()}, {yrs:.1f}년)")
print(f"  배분: 코어 {PER_CORE*len(MAJORS)/1e6:.0f}M + 폭락 2M + 현금버퍼 {CASH_BUF/1e6:.0f}M = {init/1e6:.0f}M")
print("="*64)
for nm,s in [("코어(추세)",ce),("폭락(급락)",pe),("현금버퍼",ge),("━ 합산",total)]:
    c,m,_=stats(s)
    print(f"  {nm:<12} 시작 {s.iloc[0]/1e6:5.2f}M → 종료 {s.iloc[-1]/1e6:5.2f}M  CAGR {c*100:+6.1f}%  MDD {m*100:5.0f}%")
print("="*64)
print(f"  최종자산 {total.iloc[-1]/1e6:.2f}M  (원금 {init/1e6:.0f}M, 순이익 {(total.iloc[-1]-init)/1e4:+,.0f}만원)")

# ── HWM 적립(원금 2,000만 위 고점수익 50%) → 월 인출 ──
base=cfg["skim"]["principal_krw"]; ratio=cfg["skim"]["ratio"]; reserve=0.0
for v in total:
    if v>base:
        sk=(v-base)*ratio; reserve+=sk; base=v-sk
months=yrs*12
print(f"  HWM 적립 누적 {reserve/1e4:,.0f}만원  →  월평균 인출 {reserve/months/1e4:.1f}만원 (원금보존)")
print("="*64)
