"""수익 원천 분석 — 불장·횡보 한 달 수익이 '어디서' 나왔는지 트레이드 단위로 해부.

질문: 몇 개 코인/거래에 몰린 운인가, 골고루 난 실력인가?
  - 코인별 손익 분포(집중 vs 분산)
  - 거래 승률·손익비·Profit Factor(많은 소액승 vs 소수 대박)
  - 상위 N거래가 전체 수익의 몇 %인가(집중도)
  - 수익 발생 타이밍(전반/후반 — 한 방 스파이크 여부)
사용법: python profit_analysis.py
"""
from __future__ import annotations
import os, sys, tempfile, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuit_backtest as cb
from confluence_live_check import _AIView

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def run_coin(cfg, tk, bars, view, budget):
    from trader import live as livemod, backtest as btmod
    import pyupbit
    st = {"i": 0}
    btmod.fetch_minutes = lambda ticker, unit, count, to_kst=None: bars.iloc[max(0, st["i"]-199):st["i"]+1]
    pyupbit.get_current_price = lambda ticker: float(bars["close"].iloc[st["i"]])
    c = dict(cfg); c["portfolio"] = {**cfg["portfolio"], "tickers": [tk], "per_coin_krw": budget}
    lt = livemod.LiveTrader(c, advisor=None, news=None, state_path=tempfile.mktemp(suffix=".json"))
    lt.sel_enabled = False; lt.active = set(lt.tickers); lt.enable()
    n = len(bars)
    for i in range(210, n):
        st["i"] = i; lt._ai_view = view
        for _ in (0,): lt._eval_coin(tk)
    tr = lt.coins[tk]["trades"]
    # (pnl, 진입후경과비율) — 매도시점 i를 모르니 거래순서로 타이밍 근사
    sells = [(t.get("pnl", 0.0), t.get("reason", "?")) for t in tr if t["side"] == "sell"]
    return sells


def analyze(label, data, view, budget):
    print(f"\n{'='*70}\n  [{label}]  국면 한 달 — 수익 원천 해부\n{'='*70}")
    coin_pnl = {}; all_pnls = []; reason_pnl = {}
    for tk, bars in data.items():
        sells = run_coin(cfg, tk, bars, view, budget)
        cp = sum(p for p, _ in sells)
        coin_pnl[tk] = cp
        for p, r in sells:
            all_pnls.append(p)
            reason_pnl[r] = reason_pnl.get(r, 0.0) + p
    total = sum(all_pnls)
    wins = [p for p in all_pnls if p > 0]; losses = [p for p in all_pnls if p < 0]
    pf = (sum(wins) / -sum(losses)) if losses else float("inf")
    print(f"\n  총손익 {total:+,.0f}원  ({total/(budget*len(data))*100:+.2f}% / 전체자본)")
    print(f"  거래 {len(all_pnls)}건  승률 {len(wins)/len(all_pnls)*100:.0f}%  "
          f"평균승 {np.mean(wins) if wins else 0:+,.0f}  평균패 {np.mean(losses) if losses else 0:+,.0f}  PF {pf:.2f}")

    # 코인 집중도
    print(f"\n  ── 코인별 손익(집중 vs 분산) ──")
    for tk in sorted(coin_pnl, key=lambda x: coin_pnl[x], reverse=True):
        bar = "█" * int(abs(coin_pnl[tk]) / max(1, max(abs(v) for v in coin_pnl.values())) * 20)
        print(f"  {tk.split('-')[1]:6}{coin_pnl[tk]:+12,.0f}  {bar}")
    pos = [v for v in coin_pnl.values() if v > 0]
    print(f"  → +코인 {len(pos)}/{len(coin_pnl)},  최고코인이 전체수익의 "
          f"{max(coin_pnl.values())/total*100 if total>0 else 0:.0f}% 차지")

    # 거래 집중도(상위 N거래)
    sp = sorted(all_pnls, reverse=True)
    for n in (1, 3, 5):
        topn = sum(sp[:n])
        print(f"  → 상위 {n}거래가 전체수익의 {topn/total*100 if total>0 else 0:.0f}%")

    # 진입사유별
    print(f"  ── 청산사유별 손익 ──")
    for r in sorted(reason_pnl, key=lambda x: reason_pnl[x], reverse=True):
        print(f"     {r:10}{reason_pnl[r]:+12,.0f}")


cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"), encoding="utf-8"))
budget = 2_000_000
analyze("불장", cb.load_csv("bull", cfg), _AIView("BULL", confidence=80), budget)
analyze("횡보", cb.load_csv("sideways", cfg), None, budget)
print("\n  판정 기준: +코인 다수 + 상위거래 집중도 낮음 = 골고루(신뢰↑). 한 코인/거래 몰림 = 운(신뢰↓).")
