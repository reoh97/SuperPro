"""동적 셀렉터 end-to-end 검증 — 통합된 라이브 엔진을 멀티코인 롤링 재선별로 구동.

실제 LiveTrader(_refresh_selection + _eval_coin)를 유니버스 전체에 흘려보내며,
매 rebalance마다 상위 top_k만 신규진입하게 한 결과를 '셀렉터 OFF(전체매매)'와 비교한다.
자본은 동일(총예산)하게: ON은 top_k에 몰아주고(per_coin=예산/top_k), OFF는 전체에 분산(예산/N).
사용법: python validate_dynamic.py
"""
from __future__ import annotations
import copy, os, sys, tempfile, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuit_backtest as cb
from confluence_live_check import _AIView

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

REBAL = 96   # 재선별 주기(봉)=1일


def run_portfolio(cfg, data, view, budget, sel_on, top_k):
    from trader import live as livemod, backtest as btmod
    import pyupbit
    coins = list(data)
    st = {"i": 0}
    btmod.fetch_minutes = lambda ticker, unit, count, to_kst=None: data[ticker].iloc[max(0, st["i"]-199):st["i"]+1]
    pyupbit.get_current_price = lambda ticker: float(data[ticker]["close"].iloc[st["i"]])
    c = copy.deepcopy(cfg)
    c["selector"]["enabled"] = sel_on
    c["selector"]["top_k"] = top_k
    c["selector"]["universe"] = coins
    c["portfolio"]["per_coin_krw"] = budget / (top_k if sel_on else len(coins))
    lt = livemod.LiveTrader(c, advisor=None, news=None, state_path=tempfile.mktemp(suffix=".json"))
    lt.sel_rebalance_sec = 0.0           # 케이던스는 아래 루프가 제어
    lt.enable()
    n = min(len(data[tk]) for tk in coins)
    sel_log = []
    for i in range(210, n):
        st["i"] = i
        lt._ai_view = view
        if sel_on and (i - 210) % REBAL == 0:
            lt._refresh_selection()
            sel_log.append(tuple(sorted(s.split('-')[1] for s in lt.active)))
        for tk in coins:
            lt._eval_coin(tk)
    pnl = 0.0
    for tk in coins:
        cc = lt.coins[tk]; pos = cc["position"]; grid = cc.get("grid", [])
        price = float(data[tk]["close"].iloc[n-1])
        held = (pos["size"]*price if pos else 0.0) + sum(g["size"]*price for g in grid)
        base = budget/(top_k if sel_on else len(coins))
        pnl += (cc["cash"] + held) - base
    return pnl/budget*100, sel_log


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    budget = 20_000_000
    tk_k = cfg["selector"]["top_k"]
    print("=" * 68)
    print(f"  동적 셀렉터 end-to-end (라이브 엔진, 총예산 {budget:,}, thr={cfg['confluence']['threshold']})")
    print("=" * 68)
    for label, view in [("bull", _AIView("BULL", confidence=80)), ("sideways", None)]:
        data = cb.load_csv(label, cfg)
        off, _ = run_portfolio(cfg, data, view, budget, sel_on=False, top_k=tk_k)
        for k in (2, 3):
            on, log = run_portfolio(cfg, data, view, budget, sel_on=True, top_k=k)
            picks = " ".join("/".join(p) for p in log[:4]) + (" ..." if len(log) > 4 else "")
            print(f"\n  [{label}] 셀렉터 OFF(전체{len(data)}): {off:+.2f}%   "
                  f"vs  ON top{k}: {on:+.2f}%  ({on-off:+.2f}%p)")
            print(f"        top{k} 선택이력: {picks}")


if __name__ == "__main__":
    main()
