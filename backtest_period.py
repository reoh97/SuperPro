"""기간 지정 백테스트 — 특정 구간(기본 2025-01-01~)에서 V2 시스템 검증.

코어(일봉)는 daily_data 전 구간(2023-06~2026-06) 커버 → 임의 구간 정확 검증.
새틀(15분봉)은 보유 구간만(국면별) → 2025~2026 중엔 약세구간(2026-04~06)만 존재.

출력: 코어 메이저/알트/합산 + 그냥보유(시장맥락) + 새틀(데이터 있는 구간) — 모두 슬리피지 반영.
사용법:  python backtest_period.py [시작일] [종료일]
  예)    python backtest_period.py 2025-01-01 2026-06-10
"""
from __future__ import annotations
import os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, yaml

import ratio_optimize as R
import circuit_backtest as cb
from trader import backtest as sat_bt

BASE = os.path.dirname(os.path.abspath(__file__))
CAP = 20_000_000
MAJORS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
ALTS = ["KRW-ADA", "KRW-DOGE", "KRW-TRX", "KRW-LINK", "KRW-AVAX", "KRW-DOT"]
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def slip_of(cfg, tk, mult=1.0):
    s = cfg.get("slippage", {})
    return s.get("by_coin", {}).get(tk, s.get("default_pct", 0.0)) * mult


def core_period(cfg, tickers, start, end):
    """코어 일봉 실엔진을 [start,end]로 잘라 동일가중 수익/MDD/월환산."""
    lt = cfg["longterm"]; curves = []
    for tk in tickers:
        f = os.path.join(BASE, "daily_data", f"{tk}.csv")
        if not os.path.exists(f):
            continue
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) < lt["ma_n"] + 30:
            continue
        cur = R.core_curve(df, lt, slip=slip_of(cfg, tk))   # 전체로 워밍업
        cur = cur[(cur.index >= start) & (cur.index <= end)]
        if len(cur) > 10:
            curves.append(cur / cur.iloc[0])                # 구간 시작=1
    mat = pd.concat(curves, axis=1, sort=True).ffill().dropna()
    dr = mat.pct_change().mean(axis=1).dropna()
    port = (1 + dr).cumprod()
    ret = port.iloc[-1] - 1
    mdd = ((port.cummax() - port) / port.cummax()).max()
    months = len(port) / 30.0
    mo = (1 + ret) ** (1 / months) - 1 if months > 0 else 0
    return ret, mdd, mo, months


def bh_period(tickers, start, end):
    curves = []
    for tk in tickers:
        f = os.path.join(BASE, "daily_data", f"{tk}.csv")
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        c = df["close"]; c = c[(c.index >= start) & (c.index <= end)]
        if len(c) > 10:
            curves.append(c / c.iloc[0])
    mat = pd.concat(curves, axis=1, sort=True).ffill().dropna()
    port = mat.mean(axis=1)
    return port.iloc[-1] - 1, ((port.cummax() - port) / port.cummax()).max()


def sat_window(cfg, tickers, regime_key, exec_mode):
    data = cb.load_csv(regime_key, cfg)
    data = {t: d for t, d in data.items() if t in tickers}
    if not data:
        return None, None
    rc = R._bear_gated_cfg(cfg) if regime_key == "bear" else cfg
    rets = []
    for tk, df in data.items():
        res = sat_bt.run(df, rc, slip=slip_of(cfg, tk), exec_mode=exec_mode)
        rets.append((res.end_equity - res.start_equity) / res.start_equity)
    span = next(iter(data.values())).index
    return float(np.mean(rets)), (span[0].date(), span[-1].date())


def main():
    a = sys.argv[1:]
    start = pd.Timestamp(a[0]) if len(a) > 0 else pd.Timestamp("2025-01-01")
    end = pd.Timestamp(a[1]) if len(a) > 1 else pd.Timestamp("2026-12-31")
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    lt = cfg["longterm"]; ov = lt.get("per_coin_overrides", {})
    maj_cap = sum(ov.get(t, lt["per_coin_krw"]) for t in MAJORS)
    altc_cap = sum(ov.get(t, lt["per_coin_krw"]) for t in ALTS)

    mr, mmdd, mmo, mon = core_period(cfg, MAJORS, start, end)
    ar, amdd, amo, _ = core_period(cfg, ALTS, start, end)
    bhr, bhmdd = bh_period(MAJORS + ALTS, start, end)

    print("=" * 76)
    print(f"  기간 백테스트  {start.date()} ~ {end.date()}  ({mon:.1f}개월, 현실 슬리피지)")
    print("=" * 76)
    print(f"\n  [코어 — 일봉 실엔진, 2,000만 기준]")
    print(f"  {'슬리브':<14}{'구간수익':>10}{'월환산':>9}{'MDD':>7}")
    print(f"  🪨 메이저4      {mr*100:>+9.1f}%{mmo*100:>+8.2f}%{mmdd*100:>6.0f}%")
    print(f"  🪨 알트6        {ar*100:>+9.1f}%{amo*100:>+8.2f}%{amdd*100:>6.0f}%")
    # 코어 합산(자본가중: 메이저800만 + 알트702만)
    core_ret = (maj_cap * mr + altc_cap * ar) / (maj_cap + altc_cap)
    core_mo = (1 + core_ret) ** (1 / mon) - 1 if mon > 0 else 0
    print(f"  ⚖ 코어 합산     {core_ret*100:>+9.1f}%{core_mo*100:>+8.2f}%{'':>7}  (메이저+알트 자본가중)")
    print(f"  📉 그냥보유10    {bhr*100:>+9.1f}%{'':>9}{bhmdd*100:>6.0f}%   ← 시장 맥락")

    # 코어가 총자본의 75% → 합산 시스템 근사(새틀 25%는 해당구간 15분봉 있는 국면만)
    sys75 = core_ret * 0.75   # 새틀(25%) 미반영분은 아래 별도
    print(f"\n  [새틀 — 15분봉 있는 구간만(알트, 지정가 vs 시장가)]")
    found = False
    for rk in ("bull", "sideways", "bear"):
        lim, span = sat_window(cfg, ALTS, rk, "limit")
        if lim is None:
            continue
        if not (pd.Timestamp(span[0]) >= start and pd.Timestamp(span[1]) <= end):
            continue   # 이 구간 밖이면 스킵
        found = True
        mkt, _ = sat_window(cfg, ALTS, rk, "market")
        print(f"  [{rk}] {span[0]}~{span[1]}: 지정가 {lim*100:+.2f}% / 시장가 {mkt*100:+.2f}%")
    if not found:
        print(f"  (이 기간에 해당하는 새틀 15분봉 데이터 없음 — 한국IP fetch_15m.py로 연속수집 필요)")

    print(f"\n  [요약] 코어 합산 구간 {core_ret*100:+.1f}% (월 {core_mo*100:+.2f}%) "
          f"vs 그냥보유 {bhr*100:+.1f}%")
    print(f"  ※ 코어=정확(일봉 실엔진). 새틀=구간 한정·AI게이트 제외·지정가 낙관. 과거≠미래.")


if __name__ == "__main__":
    main()
