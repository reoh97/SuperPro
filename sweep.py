"""파라미터 자동 탐색(grid sweep).

기존 백테스트 엔진(trader.backtest.run)을 그대로 재사용해, 핵심 파라미터
조합을 전부 돌려보고 '수수료 차감 순손익' 기준으로 상위 설정을 출력한다.

사용법:
    python sweep.py                # config.yaml + 기본 3000봉
    python sweep.py 5000           # 봉 개수 지정
    python sweep.py 5000 KRW-ETH   # 봉 개수 + 티커

데이터는 한 번만 받아 메모리에 두고, 조합마다 cfg만 바꿔 재평가한다.
"""
from __future__ import annotations

import copy
import itertools
import os
import sys

import yaml

from trader import backtest

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# 탐색할 파라미터 격자. (경로, 후보값들) — 경로는 cfg 딕셔너리의 중첩 키.
GRID = {
    ("regime", "adx_trend"):                       [15, 20, 25],
    ("scalp", "confirm_bars"):                     [2, 3, 4],
    ("scalp", "sideways", "min_box_pct"):          [0.015, 0.025, 0.035],
    ("scalp", "sideways", "entry_zone"):           [0.15, 0.25, 0.35],
    ("scalp", "sideways", "tp_zone"):              [0.75, 0.85, 0.95],
    ("scalp", "sideways", "sl_below"):             [0.002, 0.004, 0.008],
}


def set_path(cfg: dict, path: tuple, value) -> None:
    d = cfg
    for key in path[:-1]:
        d = d.setdefault(key, {})
    d[path[-1]] = value


def evaluate(base_cfg: dict, df, combo: dict) -> dict:
    cfg = copy.deepcopy(base_cfg)
    for path, val in combo.items():
        set_path(cfg, path, val)
    res = backtest.run(df, cfg)
    trades = res.trades
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = -sum(t.net_pnl for t in trades if t.net_pnl <= 0)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    return {
        "combo": combo,
        "net": res.end_equity - res.start_equity,
        "trades": n,
        "winrate": (len(wins) / n) if n else 0.0,
        "pf": pf,
    }


def fmt_combo(combo: dict) -> str:
    return "  ".join(f"{path[-1]}={val}" for path, val in combo.items())


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    if len(sys.argv) > 2:
        base_cfg["ticker"] = sys.argv[2]
    unit = int(base_cfg.get("timeframe_min", 1))

    print(f"\n과거 {base_cfg['ticker']} {unit}분봉 {count}개 수집 중...")
    df = backtest.fetch_minutes(base_cfg["ticker"], unit, count)
    if df is None or len(df) < 250:
        print("데이터 수집 실패 또는 부족합니다 (네트워크/티커 확인).")
        return
    print(f"수집 완료: {len(df)}봉  ({df.index[0]} ~ {df.index[-1]})")

    paths = list(GRID.keys())
    value_lists = [GRID[p] for p in paths]
    combos = list(itertools.product(*value_lists))
    print(f"조합 {len(combos)}개 평가 중...\n")

    results = []
    for i, values in enumerate(combos, 1):
        combo = dict(zip(paths, values))
        results.append(evaluate(base_cfg, df, combo))
        if i % 50 == 0:
            print(f"  ...{i}/{len(combos)}")

    # 최소 거래수 필터(통계적 의미) 후 순손익 내림차순 정렬
    MIN_TRADES = 8
    ranked = sorted(
        [r for r in results if r["trades"] >= MIN_TRADES],
        key=lambda r: r["net"], reverse=True,
    )

    print("\n" + "=" * 78)
    print(f"  상위 결과 (거래수 {MIN_TRADES}건 이상, 순손익 기준)")
    print("=" * 78)
    profitable = [r for r in ranked if r["net"] > 0]
    print(f"  수익 조합: {len(profitable)} / {len(ranked)}개 (필터 통과)\n")
    for r in ranked[:15]:
        pf = "inf" if r["pf"] == float("inf") else f"{r['pf']:.2f}"
        print(f"  {r['net']:>+10,.0f}원  거래{r['trades']:>3}  승률{r['winrate']*100:>4.0f}%  PF {pf:>5}")
        print(f"             {fmt_combo(r['combo'])}")
    print("=" * 78)
    if not profitable:
        print("  ⚠️ 이 격자에선 수익 조합이 없음 — 진입 로직 자체 개선 필요.")


if __name__ == "__main__":
    main()
