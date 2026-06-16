"""V2 보완구조 백테스트 — 실제 자본배분·종목분리·지정가·슬리피지 반영.

구성(코인당 200만, 총 2,000만):
  - 코어 메이저4 (BTC/ETH/XRP/SOL): 각 200만, 일봉 추세, 시장가(슬리피지 거의 무영향)
  - 코어 알트6              : 각 117만, 일봉 추세, 시장가
  - 새틀 알트6              : 각 83만, 15분봉 평균회귀, 지정가(슬리피지 회피)
합산수익 = 자본가중(코어메이저 800만 + 코어알트 702만 + 새틀알트 498만)/2,000만.

비교: 새틀을 시장가 vs 지정가로 돌렸을 때 합산이 어떻게 달라지나(지정가 가치).
주의: 코어=일봉3년 실엔진(정확). 새틀=국면 15분봉 3구간·AI게이트 제외(보수적)·지정가체결 다소 낙관.
사용법:  python backtest_v2.py
"""
from __future__ import annotations
import glob, os, sys, warnings
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


def core_annual(cfg, tickers, mult=1.0):
    """코어 일봉 실엔진(슬리피지) → 종목군 동일가중 연율."""
    lt = cfg["longterm"]; curves = []
    for tk in tickers:
        f = os.path.join(BASE, "daily_data", f"{tk}.csv")
        if not os.path.exists(f):
            continue
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) >= lt["ma_n"] + 30:
            curves.append(R.core_curve(df, lt, slip=slip_of(cfg, tk, mult)))
    mat = pd.concat(curves, axis=1, sort=True).ffill().dropna()
    dr = mat.pct_change().mean(axis=1).dropna()
    port = (1 + dr).cumprod()
    yrs = (port.index[-1] - port.index[0]).days / 365.25
    mdd = ((port.cummax() - port) / port.cummax()).max()
    return port.iloc[-1] ** (1 / yrs) - 1, mdd


def sat_annual(cfg, tickers, mult, exec_mode):
    """새틀 15분봉 실엔진(슬리피지·지정가) → 국면빈도가중 연율."""
    freq = R.regime_frequency(cfg)
    per = {}
    for rk in ("bull", "sideways", "bear"):
        data = cb.load_csv(rk, cfg)
        data = {t: d for t, d in data.items() if t in tickers}
        if not data:
            continue
        rc = R._bear_gated_cfg(cfg) if rk == "bear" else cfg
        rets = []
        for tk, df in data.items():
            res = sat_bt.run(df, rc, slip=slip_of(cfg, tk, mult), exec_mode=exec_mode)
            rets.append((res.end_equity - res.start_equity) / res.start_equity)
        days = max(len(next(iter(data.values()))) * cfg.get("timeframe_min", 15) / 60 / 24, 1)
        per[rk] = (1 + float(np.mean(rets))) ** (1 / days) - 1
    f = sum(freq[r] for r in per); m = sum(freq[r] / f * per[r] for r in per)
    return (1 + m) ** 365 - 1


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    lt = cfg["longterm"]; ov = lt.get("per_coin_overrides", {})
    core_tks = lt["tickers"]                          # V2.1: 메이저4
    sat_tks = cfg["selector"]["universe"]             # 알트6
    core_cap = sum(ov.get(t, lt["per_coin_krw"]) for t in core_tks)
    sat_cap = cfg["portfolio"]["per_coin_krw"] * len(sat_tks)

    print("=" * 74)
    print(f"  V2.1 보완구조 백테스트 (현실 슬리피지 · 총 {CAP:,}원)")
    print(f"  코어=메이저{len(core_tks)} (추세) / 새틀=알트{len(sat_tks)} (평균회귀·지정가)")
    print("=" * 74)
    core_ann, core_mdd = core_annual(cfg, core_tks)
    sat_lim = sat_annual(cfg, sat_tks, 1.0, "limit")
    sat_mkt = sat_annual(cfg, sat_tks, 1.0, "market")

    print(f"\n  [슬리브별 연율]  (자본비중)")
    print(f"  🪨 코어·메이저{len(core_tks)}  {core_ann*100:>+7.1f}%  (MDD {core_mdd*100:.0f}%, "
          f"{core_cap/CAP*100:.0f}%={core_cap:,}원)")
    print(f"  🛰 새틀·알트{len(sat_tks)}    지정가 {sat_lim*100:>+6.1f}% / 시장가 {sat_mkt*100:+.1f}%  "
          f"({sat_cap/CAP*100:.0f}%={sat_cap:,}원)")

    def combine(sat_ann):
        ann = (core_cap * core_ann + sat_cap * sat_ann) / CAP
        return ann, (1 + ann) ** (1 / 12) - 1
    ann_l, mo_l = combine(sat_lim)
    ann_m, mo_m = combine(sat_mkt)

    print(f"\n  [합산 V2.1 — 자본가중]")
    print(f"  {'체결방식':<14}{'연율':>9}{'월':>9}{'월 금액(2천만)':>16}")
    print(f"  {'지정가(V2)':<13}{ann_l*100:>+8.1f}%{mo_l*100:>+8.2f}%{mo_l*CAP:>+14,.0f}원")
    print(f"  {'시장가(비교)':<12}{ann_m*100:>+8.1f}%{mo_m*100:>+8.2f}%{mo_m*CAP:>+14,.0f}원")
    print(f"\n  [지정가 효과] 합산 월수익 {mo_m*100:+.2f}% → {mo_l*100:+.2f}% "
          f"(+{(mo_l-mo_m)*CAP:,.0f}원/월) — 새틀 슬리피지 회피분")
    print(f"  ※ 코어=일봉3년 실엔진(정확). 새틀=국면1구간·AI게이트제외(보수적)·지정가체결 다소 낙관.")


if __name__ == "__main__":
    main()
