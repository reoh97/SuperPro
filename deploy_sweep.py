"""자금 투입 강도(분할 횟수) vs 위험 측정.

분할 횟수가 적을수록 한 번에 더 크게 투입(공격적) → 일수익↑ 이지만 낙폭↑.
1~5분할 각각에 대해 일평균 수익률·플러스날 비율·최대낙폭(MDD)·총수익을 뽑아
'수익/위험 곡선'을 본다. 목표(일 1%)에 맞춰 어디까지 키울지 판단용.

데이터는 portfolio_sweep 캐시 재사용.

사용법:
    python deploy_sweep.py                       # 상승장(2024-12-15 이전 3000봉)
    python deploy_sweep.py 6000 ""               # 최신(약세장) 위험 점검
"""
from __future__ import annotations

import copy
import os
import sys
from collections import defaultdict

import yaml

from portfolio_sweep import load_data
from trader import portfolio

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TRANCHE_GRID = [1, 2, 3, 4, 5]


def daily_metrics(trades, budget_total):
    daily = defaultdict(float)
    for t in trades:
        daily[str(t.exit_time)[:10]] += t.net_pnl
    days = sorted(daily.keys())
    if not days:
        return None
    rets = [daily[d] / budget_total for d in days]
    pos = sum(1 for x in rets if x > 0)
    equity, peak, mdd = budget_total, budget_total, 0.0
    for d in days:
        equity += daily[d]
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
    total = sum(daily.values())
    return {
        "days": len(days), "avg": sum(rets) / len(rets),
        "pos_pct": pos / len(days), "mdd": mdd,
        "total_ret": total / budget_total,
    }


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    to_kst = (sys.argv[2] if len(sys.argv) > 2 else "2024-12-15 00:00:00") or None

    data = load_data(base_cfg, count, to_kst)
    if len(data) < 5:
        print("데이터 부족.")
        return
    label = f"{to_kst} 이전(상승장)" if to_kst else "최신(약세장)"
    print(f"수집 완료: {len(data)}종목 — {label}\n")

    print("=" * 72)
    print(f"  자금투입(분할) vs 위험   — 목표 일평균 +1.000%")
    print("=" * 72)
    print(f"  {'분할':>4}{'1회투입':>10}{'일평균':>10}{'플러스날':>9}{'MDD':>9}{'총수익':>10}")
    print("-" * 72)
    for n in TRANCHE_GRID:
        cfg = copy.deepcopy(base_cfg)
        cfg["portfolio"]["tranches"] = n
        per = cfg["portfolio"]["per_coin_krw"] / n
        trades = []
        for tk, df in data.items():
            trades.extend(portfolio._run_coin(tk, df, cfg).trades)
        m = daily_metrics(trades, cfg["portfolio"]["per_coin_krw"] * len(data))
        if not m:
            continue
        print(f"  {n:>4}{per:>10,.0f}{m['avg']*100:>9.3f}%{m['pos_pct']*100:>8.0f}%"
              f"{m['mdd']*100:>8.2f}%{m['total_ret']*100:>9.2f}%")
    print("=" * 72)
    print("  ※ 분할↓ = 1회 투입↑ = 공격적. 일평균↑ 하지만 MDD↑. 둘의 균형점 선택.")


if __name__ == "__main__":
    main()
