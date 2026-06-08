"""합의(Confluence) 진입 엔진 — 문턱(threshold) 스윕 백테스트.

다지표 합의 점수 엔진을 켜고, 진입 문턱을 바꿔가며 국면별(상승/횡보/약세) 실 KRW
10종목에 대해 순수익률·승률·손익비를 비교한다. "몇 점 이상에서 진입해야 가장 좋은가"를
데이터로 찾는 도구 — 가중치를 과하게 깎지 않고 문턱 하나만 스캔(과적합 최소화).

사용법: python confluence_sweep.py   (backtest_data 의 KRW 실데이터 필요)
"""
from __future__ import annotations

import copy
import os
import sys

import numpy as np
import yaml

import circuit_backtest as cb
from trader import backtest

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 데이터 폴더 접두사 → 표시 국면명
REGIME_SETS = [("bull", "상승(BULL)"), ("sideways", "횡보(SIDE)"), ("bear", "약세(BEAR)")]
THRESHOLDS = [80, 85, 88, 91, 94]


def run_one(df, cfg) -> tuple:
    """단일 종목 백테스트 → (순수익률%, 승률, 거래수)."""
    res = backtest.run(df, cfg)
    n = len(res.trades)
    wins = sum(1 for t in res.trades if t.net_pnl > 0)
    ret = (res.end_equity - res.start_equity) / res.start_equity if res.start_equity else 0.0
    wr = (wins / n) if n else 0.0
    return ret * 100, wr, n


def main():
    base_cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))

    print("=" * 64)
    print("  합의(Confluence) 엔진 — 진입 문턱 스윕 (실 KRW 10종목/국면)")
    print("  점수=모든 지표 합의(0~100). 문턱↑ = 합의 강할 때만 진입")
    print("=" * 64)

    # 데이터 미리 로드(국면별)
    datasets = {}
    for key, _ in REGIME_SETS:
        datasets[key] = cb.load_csv(key, base_cfg)

    for thr in THRESHOLDS:
        cfg = copy.deepcopy(base_cfg)
        cfg.setdefault("confluence", {})["enabled"] = True
        cfg["confluence"]["threshold"] = thr

        print(f"\n  ── 문턱 {thr}점 ──")
        print(f"  {'국면':<12}{'평균순익/코인':>14}{'승률':>8}{'+코인%':>8}{'평균거래':>9}")
        for key, label in REGIME_SETS:
            data = datasets.get(key) or {}
            if not data:
                print(f"  {label:<12}{'데이터 없음 (backtest_data 필요)':>30}")
                continue
            stats = [run_one(df, cfg) for df in data.values()]
            rets = np.array([s[0] for s in stats])
            wrs = np.array([s[1] for s in stats])
            ns = np.array([s[2] for s in stats])
            pos = (rets > 0).mean() * 100
            print(f"  {label:<12}{rets.mean():>+13.3f}%{wrs.mean()*100:>7.0f}%"
                  f"{pos:>7.0f}%{ns.mean():>9.1f}")

    print("\n  해석: 상승/횡보는 +, 약세는 손실 최소(0 근처)인 문턱이 최적.")
    print("  결정 후 config.yaml 의 confluence.enabled=true + threshold 설정.")


if __name__ == "__main__":
    main()
