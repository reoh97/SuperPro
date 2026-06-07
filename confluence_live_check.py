"""라이브 엔진 하이브리드 검증 — 실제 LiveTrader._eval_coin 경로로 합의 엔진을 구동.

engine_compare.py(백테스트 엔진)가 보여준 '횡보 합의 +2.9%'를 *라이브 엔진*이 그대로
재현하는지 확인한다. 라이브의 데이터/시세 함수를 과거 CSV로 교체해 봉마다 재생:
  - AI 뷰=None(불장 아님)  → 합의 엔진 활성(ai_gated). 횡보 데이터에서 +가 나와야 함.
  - AI 뷰=BULL             → 합의 OFF, 현행 추세엔진. (불장은 현행 유지 확인)

라이브 적용 전 '머니경로'가 의도대로 도는지 보는 최종 점검. 사용법: python confluence_live_check.py
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pandas as pd
import yaml

import circuit_backtest as cb

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class _AIView:
    def __init__(self, regime, risk_off=False, confidence=80):
        self.regime, self.risk_off, self.confidence = regime, risk_off, confidence
        self.reason = "(검증용 주입 뷰)"


def replay_coin(cfg, tk, bars, ai_view, budget) -> dict:
    """라이브 엔진을 한 종목·과거 봉에 재생 → 지표 dict."""
    from trader import live as livemod, backtest as btmod
    import pyupbit
    state = {"df": None, "price": None}
    btmod.fetch_minutes = lambda ticker, unit, count, to_kst=None: state["df"]
    pyupbit.get_current_price = lambda ticker: state["price"]

    c = dict(cfg)
    c["portfolio"] = {**cfg["portfolio"], "tickers": [tk], "per_coin_krw": budget}
    lt = livemod.LiveTrader(c, advisor=None, news=None, state_path=tempfile.mktemp(suffix=".json"))
    lt.enable()
    for i in range(210, len(bars)):
        state["df"] = bars.iloc[i - 199:i + 1]
        state["price"] = float(bars["close"].iloc[i])
        lt._ai_view = ai_view
        lt._eval_coin(tk)
    coin = lt.coins[tk]
    pos = coin["position"]
    grid = coin.get("grid", [])
    held = (pos["size"] * state["price"] if pos else 0.0)
    held += sum(g["size"] * state["price"] for g in grid) if grid else 0.0
    eq = coin["cash"] + held
    sells = [t for t in coin["trades"] if t["side"] == "sell"]
    sr = {}
    for t in sells:
        sr[t["reason"]] = sr.get(t["reason"], 0) + 1
    return {"ret": (eq / budget - 1) * 100, "sells": len(sells), "reasons": sr}


def run_set(cfg, ai_view, label):
    data = cb.load_csv("sideways", cfg)
    if not data:
        print(f"  {label}: 횡보 데이터 없음 (backtest_data 필요)"); return
    budget = 2_000_000
    rets, sells, allr = [], [], {}
    for tk, bars in data.items():
        m = replay_coin(cfg, tk, bars, ai_view, budget)
        rets.append(m["ret"]); sells.append(m["sells"])
        for k, v in m["reasons"].items():
            allr[k] = allr.get(k, 0) + v
    rets = np.array(rets)
    print(f"\n  [{label}]  (라이브 엔진, 횡보 실KRW {len(data)}종목)")
    print(f"    평균순익/코인 {rets.mean():+.2f}%   +코인 {(rets>0).mean()*100:.0f}%   "
          f"평균매도 {np.mean(sells):.1f}")
    print(f"    청산사유 합계 {allr}")


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    print("=" * 64)
    print("  라이브 엔진 하이브리드 검증 — LiveTrader._eval_coin 직접 구동")
    print(f"  confluence.enabled={cfg.get('confluence',{}).get('enabled')} "
          f"ai_gated={cfg.get('confluence',{}).get('ai_gated')} "
          f"threshold={cfg.get('confluence',{}).get('threshold')}")
    print("=" * 64)
    # AI 없음(=tier None) → 합의 활성. 횡보장에서 + 나와야 함(engine_compare 백테스트와 대조).
    run_set(cfg, None, "AI 불장아님 → 합의 활성")
    # AI=BULL → 합의 OFF, 현행 추세. (횡보 데이터라 진입 적고 현행과 유사해야)
    run_set(cfg, _AIView("BULL", confidence=80), "AI=BULL → 합의 OFF(현행 추세)")
    print("\n  기대: '합의 활성'이 백테스트 횡보 합의(+2.9%)와 유사하면 라이브 통합 정상.")
    print("        청산사유에 tp/sl/timeout(scalp)·trail이 보이면 합의 청산 경로 정상.")


if __name__ == "__main__":
    main()
