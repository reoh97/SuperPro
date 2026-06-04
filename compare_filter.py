"""상승장 진입필터 A/B 비교.

UP(trail) 진입에 필터(ADX상승/양봉/중기EMA위/최소ADX)를 켰을 때, UP 국면
순손익·승률·전체수익이 어떻게 변하는지 비교. 데이터는 portfolio_sweep 의 캐시를
재사용(없으면 새로 수집).

사용법:
    python compare_filter.py                       # 3000봉, 2024-12-15 이전(상승장)
    python compare_filter.py 3000 "2024-12-15 00:00:00"
    python compare_filter.py 6000 ""               # 최신(약세장)에서 방어 점검
"""
from __future__ import annotations

import copy
import os
import sys

import yaml

from portfolio_sweep import evaluate, load_data

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 비교할 필터 조합 (이름, uptrend에 덮어쓸 키)
VARIANTS = [
    ("기준(필터없음)",        {}),
    ("+ADX상승",             {"require_adx_rising": True}),
    ("+양봉",                {"require_bull_candle": True}),
    ("+중기EMA위",           {"require_above_mid": True}),
    ("+최소ADX25",           {"min_adx": 25}),
    ("ADX상승+양봉",         {"require_adx_rising": True, "require_bull_candle": True}),
    ("3종(ADX상승+양봉+중기)", {"require_adx_rising": True, "require_bull_candle": True,
                              "require_above_mid": True}),
]


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    to_kst = sys.argv[2] if len(sys.argv) > 2 else "2024-12-15 00:00:00"
    to_kst = to_kst or None

    data = load_data(base_cfg, count, to_kst)
    if len(data) < 5:
        print("데이터 부족.")
        return
    label = f"{to_kst} 이전(상승장)" if to_kst else "최신(약세장)"
    print(f"수집 완료: {len(data)}종목 — 구간: {label}\n")

    print("=" * 78)
    print(f"  {'필터':<22}{'전체':>8}{'PF':>7}{'UP순손익':>12}{'UP승률':>8}{'전체거래':>8}")
    print("=" * 78)
    base_net = None
    for name, overrides in VARIANTS:
        cfg = copy.deepcopy(base_cfg)
        u = cfg["scalp"]["uptrend"]
        u["mode"] = "trail"
        # 이전 변형의 필터가 남지 않도록 초기화
        for k in ("require_adx_rising", "require_bull_candle", "require_above_mid", "min_adx"):
            u.pop(k, None)
        u.update(overrides)
        r = evaluate(data, cfg)
        if base_net is None:
            base_net = r["net"]
        flag = ""
        if name != "기준(필터없음)":
            diff = r["net"] - base_net
            flag = f"  ({diff:+,.0f})"
        print(f"  {name:<22}{r['ret']*100:>7.2f}%{r['pf']:>7.2f}"
              f"{r['up_net']:>12,.0f}{r['up_wr']*100:>7.0f}%{r['trades']:>8}{flag}")
    print("=" * 78)
    print("  ※ UP순손익↑ + 전체↑ 면 좋은 필터. UP승률만 오르고 순손익 줄면 과한 필터.")


if __name__ == "__main__":
    main()
