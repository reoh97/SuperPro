"""상승장 '작게 자주먹기'(fixed) 익절×손절 격자 탐색.

목적: UP 청산을 fixed(고정 익절/손절)로 했을 때, 어떤 (tp, sl) 조합이
trail(추세 길게 타기) 대비 우위인지 데이터로 확인. 1% 같은 특정값 집착 없이
범위를 훑는다.

데이터(10종목, 상승장 구간)는 한 번만 받아 data/sweep_cache.pkl 에 캐시 →
이후 격자 평가는 메모리에서 즉시. (네트워크가 유일한 병목)

사용법:
    python portfolio_sweep.py                       # 기본 3000봉, 2024-12-15 이전
    python portfolio_sweep.py 3000 "2024-12-15 00:00:00"
"""
from __future__ import annotations

import copy
import itertools
import os
import pickle
import sys

import yaml

from trader import backtest, portfolio, regime

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 탐색 격자
TP_GRID = [0.005, 0.007, 0.009, 0.012, 0.015, 0.02]
SL_GRID = [0.004, 0.006, 0.008, 0.011, 0.015]


def load_data(cfg, count, to_kst):
    """10종목 분봉을 받아 캐시. (구간·봉수·종목 동일하면 캐시 재사용)"""
    tickers = cfg["portfolio"]["tickers"]
    unit = int(cfg.get("timeframe_min", 1))
    key = f"{unit}_{count}_{to_kst}_{','.join(tickers)}"
    cache_path = os.path.join(BASE, "data", "sweep_cache.pkl")
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            blob = pickle.load(f)
        if blob.get("key") == key:
            print("캐시된 데이터 사용.")
            return blob["data"]
    print(f"{len(tickers)}종목 × {unit}분봉 {count}개 ({to_kst or '최신'} 이전) 수집 중...")
    data = {}
    for tk in tickers:
        print(f"  [{tk}]...")
        df = backtest.fetch_minutes(tk, unit, count, to_kst=to_kst)
        if df is not None and len(df) >= 250:
            data[tk] = df
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump({"key": key, "data": data}, f)
    return data


def evaluate(data, cfg):
    """현재 cfg로 전 종목 시뮬 → (총수익률, PF, UP순손익, UP승률, 거래수) 반환."""
    budget_total = 0.0
    end_total = 0.0
    all_trades = []
    for tk, df in data.items():
        r = portfolio._run_coin(tk, df, cfg)
        budget_total += r.budget
        end_total += r.end_value
        all_trades.extend(r.trades)
    net = end_total - budget_total
    gw = sum(t.net_pnl for t in all_trades if t.net_pnl > 0)
    gl = -sum(t.net_pnl for t in all_trades if t.net_pnl <= 0)
    pf = (gw / gl) if gl > 0 else float("inf")
    up = [t for t in all_trades if t.regime == regime.UP]
    up_net = sum(t.net_pnl for t in up)
    up_wr = (sum(1 for t in up if t.net_pnl > 0) / len(up)) if up else 0.0
    return {
        "ret": net / budget_total if budget_total else 0.0,
        "net": net, "pf": pf, "up_net": up_net, "up_wr": up_wr,
        "trades": len(all_trades),
    }


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    to_kst = sys.argv[2] if len(sys.argv) > 2 else "2024-12-15 00:00:00"

    data = load_data(base_cfg, count, to_kst)
    if len(data) < 5:
        print("데이터 부족.")
        return
    print(f"수집 완료: {len(data)}종목\n")

    # 기준선: trail
    trail_cfg = copy.deepcopy(base_cfg)
    trail_cfg["scalp"]["uptrend"]["mode"] = "trail"
    base = evaluate(data, trail_cfg)
    print("=" * 70)
    print(f"  [기준선] trail (추세 길게)")
    print(f"    전체 {base['ret']*100:+.2f}%  PF {base['pf']:.2f}  "
          f"UP순손익 {base['up_net']:+,.0f}  UP승률 {base['up_wr']*100:.0f}%  거래 {base['trades']}")
    print("=" * 70)

    # fixed 격자
    results = []
    for tp, sl in itertools.product(TP_GRID, SL_GRID):
        cfg = copy.deepcopy(base_cfg)
        u = cfg["scalp"]["uptrend"]
        u["mode"], u["tp_pct"], u["sl_pct"] = "fixed", tp, sl
        r = evaluate(data, cfg)
        r["tp"], r["sl"] = tp, sl
        results.append(r)

    results.sort(key=lambda r: r["net"], reverse=True)
    print(f"\n  fixed(작게 자주먹기) 격자 {len(results)}조합 — 전체수익 상위:")
    print(f"  {'익절':>6}{'손절':>6}{'전체':>9}{'PF':>7}{'UP순손익':>12}{'UP승률':>8}{'거래':>6}")
    for r in results[:12]:
        flag = "  ← trail 초과" if r["net"] > base["net"] else ""
        print(f"  {r['tp']*100:>5.1f}%{r['sl']*100:>5.1f}%{r['ret']*100:>8.2f}%"
              f"{r['pf']:>7.2f}{r['up_net']:>12,.0f}{r['up_wr']*100:>7.0f}%{r['trades']:>6}{flag}")

    beat = [r for r in results if r["net"] > base["net"]]
    print("\n" + "-" * 70)
    print(f"  trail(+{base['ret']*100:.2f}%) 초과 조합: {len(beat)} / {len(results)}")
    up_beat = [r for r in results if r["up_net"] > base["up_net"]]
    print(f"  UP국면만 trail 초과: {len(up_beat)} / {len(results)}")


if __name__ == "__main__":
    main()
