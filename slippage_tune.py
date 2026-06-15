"""새틀라이트 '슬리피지 넘기' 튜닝 — 비용(수수료+슬리피지)을 넘는 설정 찾기.

슬리피지 반영 시 새틀(잦은 단타)이 적자전환했다(slippage_test). 원인: 익절폭(0.5%)이
왕복비용(수수료0.1%+슬리피지0.2~0.3%)에 거의 먹힘. 해법 두 레버를 스윕:
  - scalp.min_profit_buffer : 진입 최소익절 문턱(이만큼 못 먹을 자리는 진입 안 함) ↑
  - scalp.sideways.quick_tp_pct : 박스 빠른익절 목표폭 ↑
→ "더 크게 먹을 자리만, 덜 자주" 거래 = 슬리피지 노출↓ + 건당 마진↑.

현실 슬리피지(config.slippage ×1) 기준, 국면별(AI게이트 근사) 새틀 순익·거래수를 그리드로 본다.
사용법:  python slippage_tune.py
주의: 국면별 1구간 표본 → 과적합 경계. 방향성 확인용. 과거≠미래.
"""
from __future__ import annotations
import copy, os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, yaml

import ratio_optimize as R
import circuit_backtest as cb
from trader import backtest as sat_bt

BASE = os.path.dirname(os.path.abspath(__file__))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def slip_of(cfg, tk, mult=1.0):
    s = cfg.get("slippage", {})
    return s.get("by_coin", {}).get(tk, s.get("default_pct", 0.0)) * mult


def sat_eval(cfg, buffer, quick, tickers=None, mult=1.0):
    """주어진 buffer/quick 설정으로 국면별 새틀 순익·거래수(현실 슬리피지)."""
    c = copy.deepcopy(cfg)
    c.setdefault("scalp", {})["min_profit_buffer"] = buffer
    c["scalp"].setdefault("sideways", {})["quick_tp_pct"] = quick
    freq = R.regime_frequency(cfg)
    per, trades = {}, 0
    for rk in ("bull", "sideways", "bear"):
        data = cb.load_csv(rk, c)
        if tickers:
            data = {t: d for t, d in data.items() if t in tickers}
        if not data:
            continue
        rc = R._bear_gated_cfg(c) if rk == "bear" else c
        rets = []
        for tk, df in data.items():
            res = sat_bt.run(df, rc, slip=slip_of(cfg, tk, mult))
            rets.append((res.end_equity - res.start_equity) / res.start_equity)
            trades += len(res.trades)
        days = max(len(next(iter(data.values()))) * cfg.get("timeframe_min", 15) / 60 / 24, 1)
        per[rk] = {"ret": float(np.mean(rets)), "mean_d": (1 + float(np.mean(rets))) ** (1 / days) - 1}
    tot_f = sum(freq[r] for r in per)
    m = sum((freq[r] / tot_f) * per[r]["mean_d"] for r in per)
    annual = (1 + m) ** 365 - 1
    return annual, per, trades


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    buffers = [0.002, 0.004, 0.006, 0.008]      # 현행 0.002
    quicks = [0.005, 0.010, 0.015]              # 현행 0.005
    hdr = "buf\\quick"

    print("=" * 78)
    print("  새틀 '슬리피지 넘기' 튜닝 — 현실 슬리피지(×1) 기준, 국면가중 연율 + 총거래수")
    print("=" * 78)
    print(f"  현행: buffer 0.002 / quick 0.005 → (slippage_test 기준 적자)")
    print(f"\n  {hdr:<13}" + "".join(f"{q*100:>10.1f}%" for q in quicks))
    best = None
    grid = {}
    for b in buffers:
        cells = []
        for q in quicks:
            ann, per, tr = sat_eval(cfg, b, q)
            grid[(b, q)] = (ann, per, tr)
            cells.append(f"{ann*100:>+8.1f}%")
            if best is None or ann > best[0]:
                best = (ann, b, q, per, tr)
        print(f"  buf {b*100:>4.1f}%      " + "".join(f"{c:>10}" for c in cells))

    # 거래수 그리드(빈도 감소 확인)
    print(f"\n  [총 거래수]  (적을수록 슬리피지 노출↓)")
    print(f"  {hdr:<13}" + "".join(f"{q*100:>10.1f}%" for q in quicks))
    for b in buffers:
        print(f"  buf {b*100:>4.1f}%      " + "".join(f"{grid[(b,q)][2]:>10}" for q in quicks))

    ann, b, q, per, tr = best
    print(f"\n  [최적] buffer {b*100:.1f}% / quick {q*100:.1f}% → 연율 {ann*100:+.1f}% (거래 {tr}건)")
    print(f"    국면별: " + " · ".join(f"{lbl} {per[r]['ret']*100:+.2f}%"
          for r, lbl in [('bull','불'), ('sideways','횡'), ('bear','약')] if r in per))

    # 메이저 4종목만(슬리피지 최소) 비교
    majors = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
    am, pm, trm = sat_eval(cfg, b, q, tickers=majors)
    print(f"\n  [메이저4 한정] 같은 설정 → 연율 {am*100:+.1f}% (거래 {trm}건)  "
          f"{'← 메이저가 더 나음' if am > ann else ''}")

    print(f"\n  [현행 대비] 현행(0.2/0.5) {grid[(0.002,0.005)][0]*100:+.1f}% → "
          f"최적 {ann*100:+.1f}%  (슬리피지 넘김 {'성공 ✅' if ann>0 else '실패 ❌'})")
    print(f"  ※ 국면 1구간 표본 → 과적합 경계. AI게이트 제외(보수적). 실거래 전 모의 관찰.")


if __name__ == "__main__":
    main()
