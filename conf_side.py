import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, yaml, copy
from trader import backtest as sat_bt
cfg=yaml.safe_load(open("config.yaml"))
def slip_of(tk): s=cfg["slippage"]; return s["by_coin"].get(tk,s["default_pct"])
ALT=["KRW-ADA","KRW-DOGE","KRW-TRX","KRW-LINK","KRW-AVAX","KRW-DOT"]
dbtc=pd.read_csv("daily_data/KRW-BTC.csv",index_col=0,parse_dates=True)
ma=dbtc["close"].rolling(200).mean(); sl=ma.diff(20)
up=(dbtc["close"]>ma)&(sl>0); dn=(dbtc["close"]<ma)&(sl<0)
reg={}
for d in dbtc.index:
    reg[d.date()]="up" if up.get(d,False) else ("down" if dn.get(d,False) else "side")
c=copy.deepcopy(cfg); c["confluence"]["enabled"]=True
buckets={"up":[],"side":[],"down":[]}
for tk in ALT:
    df=pd.read_csv(f"min15_data/{tk}.csv",index_col=0,parse_dates=True)
    res=sat_bt.run(df,c,slip=slip_of(tk),exec_mode="limit")
    for t in res.trades:
        d=pd.Timestamp(t.entry_time).date()
        buckets[reg.get(d,"side")].append(t.ret_pct)
    print(f"  {tk} 완료 (거래 {len(res.trades)})", flush=True)
print("\n합의 엔진 — 진입 국면별 거래 성과 (지정가, 알트6)", flush=True)
for r in ["up","side","down"]:
    a=np.array(buckets[r]); n=len(a)
    if n: print(f"  {r:5}: 거래 {n:5}  평균 {a.mean()*100:+.3f}%  승률 {(a>0).mean()*100:.0f}%  합계 {a.sum()*100:+.0f}%", flush=True)
