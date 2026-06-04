"""하락장(DOWN) 반등 캐치 검증 — 약세장 데이터에서 과매도 반등이 수익나는지.

"하락장에도 분봉엔 상승 그림이 있다 → 그걸 먹을 수 있나?"를 데이터로 답한다.
DOWN 국면 진입 파라미터(과매도RSI × 익절 × 손절)를 격자로 훑고,
DOWN 국면 거래만 따로 집계(순손익·승률·건수).

사용법:
    python down_sweep.py            # 최신 약세장 6000봉
    python down_sweep.py 8000
"""
from __future__ import annotations

import copy
import itertools
import os
import sys

import yaml

from portfolio_sweep import load_data
from trader import portfolio, regime

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RSI_GRID = [22, 26, 30]
TP_GRID = [0.010, 0.015, 0.020, 0.030]
SL_GRID = [0.008, 0.012, 0.018]


def _days(data):
    df = next(iter(data.values()))
    return max((df.index[-1] - df.index[0]).total_seconds() / 86400.0, 1.0)


def down_metrics(data, cfg):
    trades = []
    for tk, df in data.items():
        trades.extend(portfolio._run_coin(tk, df, cfg).trades)
    down = [t for t in trades if t.regime == regime.DOWN]
    net = sum(t.net_pnl for t in down)
    wins = sum(1 for t in down if t.net_pnl > 0)
    wr = (wins / len(down)) if down else 0.0
    return net, len(down), wr


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        base = yaml.safe_load(f)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    data = load_data(base, count, None)   # 최신(약세장)
    if len(data) < 5:
        print("데이터 부족."); return
    days = _days(data)
    budget = float(base["portfolio"]["per_coin_krw"]) * len(data)
    print(f"수집 {len(data)}종목 (최신 약세장 {count}봉 ≈ {days:.0f}일)\n")

    results = []
    for rsi, tp, sl in itertools.product(RSI_GRID, TP_GRID, SL_GRID):
        cfg = copy.deepcopy(base)
        d = cfg["scalp"]["downtrend"]
        d["enabled"], d["rsi_buy"], d["tp_pct"], d["sl_pct"] = True, rsi, tp, sl
        net, n, wr = down_metrics(data, cfg)
        results.append({"rsi": rsi, "tp": tp, "sl": sl, "net": net, "n": n, "wr": wr,
                        "daily": net / budget / days * 100})

    results.sort(key=lambda r: r["net"], reverse=True)
    print("=" * 72)
    print(f"  하락장(DOWN) 반등 단타 — DOWN전략의 '일평균 기여' 기준 상위")
    print(f"  (총자본 {budget:,.0f}원, {days:.0f}일)")
    print("=" * 72)
    print(f"  {'RSI<':>5}{'익절':>7}{'손절':>7}{'일평균':>9}{'DOWN총손익':>13}{'건수':>6}{'승률':>7}")
    for r in results[:12]:
        print(f"  {r['rsi']:>5}{r['tp']*100:>6.1f}%{r['sl']*100:>6.1f}%{r['daily']:>+8.3f}%"
              f"{r['net']:>13,.0f}{r['n']:>6}{r['wr']*100:>6.0f}%")
    print("-" * 72)
    prof = [r for r in results if r["net"] > 0 and r["n"] >= 5]
    print(f"  수익+거래5건이상 조합: {len(prof)} / {len(results)}   (목표 일 +1.000%)")
    if not prof:
        print("  ⚠️ 하락장 반등으로 수익나는 조합 없음 — 진입로직 자체 개선 필요.")
    print("=" * 72)


if __name__ == "__main__":
    main()
