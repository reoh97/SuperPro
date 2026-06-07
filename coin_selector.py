"""종목 선정 분석 — 라이브 엔진(하이브리드)으로 종목별 성과를 뽑아 랭킹.

'몇 종목이 이상적인가 / 어떤 종목을 고를까'에 데이터로 답한다. 거래는 많을 필요 없고
'제대로 된 한 방'이 목표 → 종목별 (불장+횡보) 순익·승률·거래당손익·낙폭을 본다.
약세는 전 종목 0거래(방어)라 변별력이 없어 제외. 사용법: python coin_selector.py
"""
from __future__ import annotations
import os, sys, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuit_backtest as cb
from confluence_live_check import replay_coin, _AIView

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def _detail(cfg, tk, bars, view, budget):
    """replay_coin 확장: pnl 기반 승률/거래당손익/최악거래까지."""
    from trader import live as livemod, backtest as btmod
    import pyupbit, tempfile
    state = {"df": None, "price": None}
    btmod.fetch_minutes = lambda ticker, unit, count, to_kst=None: state["df"]
    pyupbit.get_current_price = lambda ticker: state["price"]
    c = dict(cfg); c["portfolio"] = {**cfg["portfolio"], "tickers": [tk], "per_coin_krw": budget}
    lt = livemod.LiveTrader(c, advisor=None, news=None, state_path=tempfile.mktemp(suffix=".json"))
    lt.enable()
    for i in range(210, len(bars)):
        state["df"] = bars.iloc[i-199:i+1]; state["price"] = float(bars["close"].iloc[i])
        lt._ai_view = view; lt._eval_coin(tk)
    coin = lt.coins[tk]; pos = coin["position"]; grid = coin.get("grid", [])
    held = (pos["size"]*state["price"] if pos else 0.0) + sum(g["size"]*state["price"] for g in grid)
    eq = coin["cash"] + held
    pnls = [t["pnl"] for t in coin["trades"] if t["side"]=="sell" and "pnl" in t]
    return {"ret": (eq/budget-1)*100, "n": len(pnls),
            "wins": sum(1 for p in pnls if p>0), "pnls": pnls}


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    budget = 2_000_000
    bull = cb.load_csv("bull", cfg); side = cb.load_csv("sideways", cfg)
    coins = sorted(set(bull) & set(side))
    print("="*78)
    print("  종목 선정 분석 — 라이브 하이브리드 (불장+횡보, 코인당 200만)")
    print("="*78)
    rows = []
    for tk in coins:
        b = _detail(cfg, tk, bull[tk], _AIView("BULL", confidence=80), budget)
        s = _detail(cfg, tk, side[tk], None, budget)
        comb = b["ret"] + s["ret"]                 # 두 국면 합산 순익(%)
        n = b["n"] + s["n"]; w = b["wins"] + s["wins"]
        wr = (w/n*100) if n else 0.0
        allp = b["pnls"] + s["pnls"]
        avgp = (np.mean(allp) if allp else 0.0)    # 거래당 평균손익(원)
        worst = (min(allp) if allp else 0.0)
        rows.append((tk, comb, b["ret"], s["ret"], n, wr, avgp, worst))
    rows.sort(key=lambda r: r[1], reverse=True)
    print(f"\n  {'종목':<10}{'합산%':>8}{'불장%':>8}{'횡보%':>8}{'거래':>5}{'승률':>7}{'거래당손익':>12}{'최악거래':>11}")
    print("  " + "-"*74)
    for tk, comb, br, sr, n, wr, avgp, worst in rows:
        print(f"  {tk:<10}{comb:>+8.2f}{br:>+8.2f}{sr:>+8.2f}{n:>5}{wr:>6.0f}%{avgp:>+12,.0f}{worst:>+11,.0f}")
    # 누적 포트폴리오: 상위 N종목만 골랐을 때 평균순익/코인 & 변동성
    print("\n  ── 상위 N종목만 선택 시 (균등배분 가정) ──")
    print(f"  {'N':>3}{'평균순익/코인':>14}{'표준편차':>10}{'+종목비율':>10}{'총거래':>8}")
    combs = [r[1] for r in rows]; ns = [r[4] for r in rows]
    for N in range(2, len(rows)+1):
        sub = np.array(combs[:N])
        print(f"  {N:>3}{sub.mean():>+13.2f}%{sub.std():>9.1f}%{(sub>0).mean()*100:>8.0f}%{sum(ns[:N]):>8}")


if __name__ == "__main__":
    main()
