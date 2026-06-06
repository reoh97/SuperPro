"""엔진 정면 비교 — 현행(국면별 전략) vs 합의(Confluence) 엔진.

같은 실 KRW 데이터(국면별 10종목)에 두 엔진을 그대로 돌려 '수익률·MDD·승률·손익비·거래수'를
나란히 비교한다. 수익률만 보면 위험을 못 보니, 청산 기준 누적자산으로 MDD(최대낙폭)도 같이 본다.
→ "합의 엔진을 라이브에 켜도 되는가"의 최종 의사결정 근거.

주의: 백테스트엔 AI 장세 게이트가 없어 약세장 UP 차단이 빠진다(라이브는 방어됨).
      따라서 약세 수치는 두 엔진 모두 '게이트 없는 최악'으로 보수적으로 읽을 것.

사용법: python engine_compare.py [threshold]   (기본 75)
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
REGIME_SETS = [("bull", "상승(BULL)"), ("sideways", "횡보(SIDE)"), ("bear", "약세(BEAR)")]

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def metrics(res) -> dict:
    """백테스트 결과 → 지표. MDD는 '청산 시점 누적자산' 곡선 기준(체결 단위 낙폭)."""
    trades = res.trades
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    ret = (res.end_equity - res.start_equity) / res.start_equity if res.start_equity else 0.0
    wr = (len(wins) / n) if n else 0.0
    gw = sum(t.net_pnl for t in wins)
    gl = -sum(t.net_pnl for t in losses)
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)
    # 청산 단위 누적자산 곡선 → MDD
    eq = res.start_equity
    peak = eq
    mdd = 0.0
    for t in trades:
        eq += t.net_pnl
        peak = max(peak, eq)
        if peak > 0:
            mdd = min(mdd, (eq - peak) / peak)
    return {"ret": ret * 100, "wr": wr * 100, "pf": pf, "mdd": mdd * 100, "n": n}


def agg(data, cfg) -> dict:
    """종목들 평균 지표."""
    ms = [metrics(backtest.run(df, cfg)) for df in data.values()]
    rets = np.array([m["ret"] for m in ms])
    pfs = [m["pf"] for m in ms if m["pf"] != float("inf")]
    return {
        "ret": rets.mean(),
        "pos": (rets > 0).mean() * 100,
        "wr": np.mean([m["wr"] for m in ms]),
        "pf": (np.mean(pfs) if pfs else float("inf")),
        "mdd": np.mean([m["mdd"] for m in ms]),
        "n": np.mean([m["n"] for m in ms]),
    }


def fmt(m) -> str:
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    return (f"{m['ret']:>+8.2f}%{m['pos']:>6.0f}%{m['wr']:>6.0f}%"
            f"{pf:>7}{m['mdd']:>8.2f}%{m['n']:>7.1f}")


def main():
    thr = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0
    base = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))

    cur = copy.deepcopy(base)
    cur.setdefault("confluence", {})["enabled"] = False     # 현행 국면별 전략

    conf = copy.deepcopy(base)
    conf.setdefault("confluence", {})["enabled"] = True      # 합의 엔진(전 국면)
    conf["confluence"]["threshold"] = thr
    conf["confluence"]["regimes"] = None

    print("=" * 78)
    print(f"  엔진 정면 비교 — 현행 vs 합의(threshold {thr:.0f})   [실 KRW 10종목/국면]")
    print("=" * 78)
    head = f"  {'엔진/국면':<14}{'순익/코인':>9}{'+코인':>6}{'승률':>6}{'PF':>7}{'MDD':>8}{'거래':>7}"
    for key, label in REGIME_SETS:
        data = cb.load_csv(key, base)
        print("-" * 78)
        if not data:
            print(f"  {label}: 데이터 없음 (backtest_data 필요)"); continue
        print(f"  ▸ {label}")
        print(head)
        mc = agg(data, cur)
        mf = agg(data, conf)
        print(f"  {'  현행':<14}{fmt(mc)}")
        print(f"  {'  합의(전국면)':<14}{fmt(mf)}")
        dr = mf["ret"] - mc["ret"]
        dm = mf["mdd"] - mc["mdd"]
        # 위험조정: 순익↑ & MDD 악화가 작아야 진짜 우세
        if dr > 0.1 and dm >= -0.5:
            verdict = "합의 우세"
        elif dr > 0.1:
            verdict = "합의 고수익·고위험"
        elif dr < -0.1:
            verdict = "현행 우세"
        else:
            verdict = "동률"
        print(f"  {'  Δ(합의-현행)':<14}{dr:>+8.2f}%{'':>6}{'':>6}{'':>7}{dm:>+8.2f}%   → {verdict}")
    print("=" * 78)
    print("  읽는 법: 순익↑·MDD작을수록(0에 가까움) 좋음. PF>1=수익구조.")
    print("  결정: 상승·횡보에서 합의가 순익↑ & MDD 악화 적으면 라이브 적용 후보.")


if __name__ == "__main__":
    main()
