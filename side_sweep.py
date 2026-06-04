"""횡보장(SIDEWAYS) 박스 전략 — 약세장에서 일 기준 수익 검증.

약세장 시간의 대부분이 SIDEWAYS(출렁임)로 분류되고, '분봉의 반등'은 거기서
박스 전략이 먹는다. 박스 핵심 파라미터(빠른익절폭 × 진입존 × 손절폭)를 격자로
훑어, SIDEWAYS 일평균 기여와 '약세장 전체' 일평균을 본다 (목표 일 +1%).

데이터는 portfolio_sweep 캐시(약세장) 재사용.

사용법:
    python side_sweep.py            # 최신 약세장 6000봉
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

QTP_GRID = [0.003, 0.005, 0.008, 0.012]   # 빠른익절 폭
ZONE_GRID = [0.15, 0.25, 0.35]            # 박스 하단 진입존
SLB_GRID = [0.003, 0.006, 0.010]          # 박스하단 아래 손절폭


def _days(data):
    df = next(iter(data.values()))
    return max((df.index[-1] - df.index[0]).total_seconds() / 86400.0, 1.0)


def metrics(data, cfg, budget, days):
    trades = []
    for tk, df in data.items():
        trades.extend(portfolio._run_coin(tk, df, cfg).trades)
    side = [t for t in trades if t.regime == regime.SIDEWAYS]
    side_net = sum(t.net_pnl for t in side)
    side_w = sum(1 for t in side if t.net_pnl > 0)
    total_net = sum(t.net_pnl for t in trades)
    return {
        "side_daily": side_net / budget / days * 100,
        "side_net": side_net, "side_n": len(side),
        "side_wr": (side_w / len(side)) if side else 0.0,
        "total_daily": total_net / budget / days * 100,
        "total_net": total_net,
    }


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        base = yaml.safe_load(f)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    data = load_data(base, count, None)
    if len(data) < 5:
        print("데이터 부족."); return
    days = _days(data)
    budget = float(base["portfolio"]["per_coin_krw"]) * len(data)
    print(f"수집 {len(data)}종목 (최신 약세장 {count}봉 ≈ {days:.0f}일)\n")

    rows = []
    for qtp, zone, slb in itertools.product(QTP_GRID, ZONE_GRID, SLB_GRID):
        cfg = copy.deepcopy(base)
        s = cfg["scalp"]["sideways"]
        s["quick_tp_pct"], s["entry_zone"], s["sl_below"] = qtp, zone, slb
        m = metrics(data, cfg, budget, days)
        m.update(qtp=qtp, zone=zone, slb=slb)
        rows.append(m)

    rows.sort(key=lambda r: r["total_daily"], reverse=True)
    print("=" * 80)
    print(f"  횡보 박스 — 약세장 일평균 (총자본 {budget:,.0f}, {days:.0f}일, 목표 일 +1.000%)")
    print("=" * 80)
    print(f"  {'빠른익절':>8}{'진입존':>7}{'손절':>7}{'SIDE일평균':>11}{'전체일평균':>11}{'SIDE건수':>9}{'SIDE승률':>9}")
    for r in rows[:14]:
        print(f"  {r['qtp']*100:>7.1f}%{r['zone']:>7.2f}{r['slb']*100:>6.1f}%"
              f"{r['side_daily']:>+10.3f}%{r['total_daily']:>+10.3f}%{r['side_n']:>9}{r['side_wr']*100:>8.0f}%")
    print("-" * 80)
    prof = [r for r in rows if r["total_daily"] > 0]
    print(f"  약세장 전체 플러스 조합: {len(prof)} / {len(rows)}")
    print("=" * 80)


if __name__ == "__main__":
    main()
