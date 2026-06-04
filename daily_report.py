"""일일 성과 리포트 — 목표(일 1%)를 측정 가능하게.

총수익이 아니라 '하루 단위'로 쪼개서:
  - 일평균 수익률, 플러스 난 날 비율, 최고/최악일, 최대낙폭(MDD)
을 본다. 손실이 어느 날·어느 국면에서 나는지 조준하기 위함.

데이터는 portfolio_sweep 캐시 재사용(상승장 구간). 약세장은 to_kst="" 로.

사용법:
    python daily_report.py                       # 상승장(2024-12-15 이전 3000봉)
    python daily_report.py 6000 ""               # 최신(약세장)
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import yaml

from portfolio_sweep import load_data
from trader import portfolio, regime

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    to_kst = (sys.argv[2] if len(sys.argv) > 2 else "2024-12-15 00:00:00") or None

    data = load_data(cfg, count, to_kst)
    if len(data) < 5:
        print("데이터 부족.")
        return

    budget_total = 0.0
    trades = []
    for tk, df in data.items():
        r = portfolio._run_coin(tk, df, cfg)
        budget_total += r.budget
        trades.extend(r.trades)

    # 청산일(exit_time) 기준으로 일일 실현손익 집계
    daily = defaultdict(float)
    daily_reg = defaultdict(lambda: defaultdict(float))
    for t in trades:
        day = str(t.exit_time)[:10]
        daily[day] += t.net_pnl
        daily_reg[day][t.regime] += t.net_pnl

    days = sorted(daily.keys())
    if not days:
        print("거래 없음.")
        return

    rets = [daily[d] / budget_total for d in days]   # 그날 손익 / 총자본
    pos_days = sum(1 for x in rets if x > 0)
    avg = sum(rets) / len(rets)
    best_d = max(days, key=lambda d: daily[d])
    worst_d = min(days, key=lambda d: daily[d])

    # 누적 자본·최대낙폭(MDD)
    equity = budget_total
    peak = equity
    mdd = 0.0
    for d in days:
        equity += daily[d]
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)

    label = f"{to_kst} 이전" if to_kst else "최신(약세장)"
    print("\n" + "=" * 64)
    print(f"  일일 성과 리포트  ({len(data)}종목, {label}, {len(days)}일)")
    print("=" * 64)
    print(f"  총자본          : {budget_total:>14,.0f} 원")
    print(f"  일평균 수익률    : {avg*100:>+8.3f}%   (목표 +1.000%)")
    print(f"  플러스 난 날     : {pos_days}/{len(days)}  ({pos_days/len(days)*100:.0f}%)")
    print(f"  최고일          : {best_d}  {daily[best_d]/budget_total*100:>+.2f}%")
    print(f"  최악일          : {worst_d}  {daily[worst_d]/budget_total*100:>+.2f}%")
    print(f"  최대낙폭(MDD)   : {mdd*100:>7.2f}%")
    print("-" * 64)
    print(f"  {'날짜':<12}{'일손익':>12}{'수익률':>9}   국면별")
    for d in days:
        rmap = daily_reg[d]
        regs = "  ".join(f"{k}:{v:+,.0f}" for k, v in sorted(rmap.items()))
        mark = "✅" if daily[d] > 0 else "🔻"
        print(f"  {d:<12}{daily[d]:>12,.0f}{daily[d]/budget_total*100:>8.2f}% {mark} {regs}")
    print("=" * 64)
    # 손실일의 국면 분포(어디서 잃는지)
    loss_reg = defaultdict(float)
    for d in days:
        if daily[d] <= 0:
            for k, v in daily_reg[d].items():
                loss_reg[k] += v
    print("  손실에 기여한 국면(마이너스 날들 합산):")
    for k, v in sorted(loss_reg.items(), key=lambda kv: kv[1]):
        print(f"    {k:<9}: {v:>+12,.0f} 원")
    print("=" * 64)


if __name__ == "__main__":
    main()
