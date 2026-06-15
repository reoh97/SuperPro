"""슬리피지 영향 분석 — "수수료·슬리피지 다 떼면 진짜 얼마 남나".

기존 백테스트는 '현재가 즉시 체결'을 가정해 낙관적이었다. 이 스크립트는 코인별 편도
슬리피지(config.slippage)를 매수 +/매도 - 로 반영해, 무(0) vs 현실 vs 보수(2배) 시나리오로
코어·새틀·합산 수익이 얼마나 깎이는지 보여준다.

  - 코어: 일봉 3년 실엔진(R.core_curve, slip 반영)
  - 새틀: 국면별 15분봉 실엔진(trader.backtest.run, slip 반영) — 잦은 단타라 슬리피지에 가장 민감
  - 합산 75/25 → 월환산 + 2,000만원 금액

사용법:  python slippage_test.py
주의: 슬리피지·국면빈도는 추정. 새틀=국면 1구간 표본(AI게이트 제외 보수적). 과거≠미래.
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
W_CORE, W_SAT = 0.75, 0.25
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def slip_of(cfg, tk, mult):
    s = cfg.get("slippage", {})
    return s.get("by_coin", {}).get(tk, s.get("default_pct", 0.0)) * mult


def core_annual(cfg, mult):
    lt = cfg["longterm"]; curves = []
    for f in sorted(glob.glob(os.path.join(BASE, "daily_data", "*.csv"))):
        tk = os.path.basename(f)[:-4]
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) >= lt["ma_n"] + 30:
            curves.append(R.core_curve(df, lt, slip=slip_of(cfg, tk, mult)))
    mat = pd.concat(curves, axis=1, sort=True).ffill().dropna()
    dr = mat.pct_change().mean(axis=1).dropna()
    port = (1 + dr).cumprod()
    years = (port.index[-1] - port.index[0]).days / 365.25
    return port.iloc[-1] ** (1 / years) - 1


def sat_annual_and_regimes(cfg, mult, freq):
    """국면별 새틀 구간수익(슬리피지 반영) + 빈도가중 연율."""
    per = {}
    for rk in ("bull", "sideways", "bear"):
        data = cb.load_csv(rk, cfg)
        if not data:
            continue
        rcfg = R._bear_gated_cfg(cfg) if rk == "bear" else cfg   # 약세=AI게이트 근사(UP차단)
        rets = []
        for tk, df in data.items():
            res = sat_bt.run(df, rcfg, slip=slip_of(cfg, tk, mult))
            rets.append((res.end_equity - res.start_equity) / res.start_equity)
        wret = float(np.mean(rets))
        # 구간 길이(일) 추정 → 일평균
        days = max(len(next(iter(data.values()))) * cfg.get("timeframe_min", 15) / 60 / 24, 1)
        per[rk] = {"ret": wret, "mean_d": (1 + wret) ** (1 / days) - 1}
    tot_f = sum(freq[r] for r in per)
    m = sum((freq[r] / tot_f) * per[r]["mean_d"] for r in per)
    annual = (1 + m) ** 365 - 1
    return annual, per


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    freq = R.regime_frequency(cfg)
    scen = [("무(0)", 0.0), ("현실(1배)", 1.0), ("보수(2배)", 2.0)]

    print("=" * 76)
    print("  슬리피지 영향 — 수수료(0.1%)에 더해 코인별 편도 슬리피지 반영")
    print("=" * 76)
    print(f"  슬리피지: 메이저 BTC/ETH 0.05% ~ 소형알트 0.15% (config.slippage, 편도)")
    print(f"\n  {'시나리오':<11}{'코어 연율':>10}{'새틀 연율':>10}{'합산 연율':>10}{'합산 월':>9}{'월 금액(2천만)':>16}")
    rows = []
    for name, mult in scen:
        ca = core_annual(cfg, mult)
        sa, per = sat_annual_and_regimes(cfg, mult, freq)
        comb = W_CORE * ca + W_SAT * sa
        mo = (1 + comb) ** (1 / 12) - 1
        print(f"  {name:<12}{ca*100:>+9.1f}%{sa*100:>+9.1f}%{comb*100:>+9.1f}%"
              f"{mo*100:>+8.2f}%{mo*CAP:>+14,.0f}원")
        rows.append((name, ca, sa, comb, mo, per))

    # 새틀 국면별 잠식(슬리피지에 가장 민감)
    print(f"\n  [새틀라이트 국면별 구간수익 — 슬리피지 잠식]")
    print(f"  {'국면':<10}{'무(0)':>10}{'현실':>10}{'보수(2배)':>11}")
    for r, lbl in [("bull", "불장"), ("sideways", "횡보"), ("bear", "약세")]:
        vals = [dict(rows[i][5]).get(r, {}).get("ret", float('nan')) for i in range(3)]
        print(f"  {lbl:<10}{vals[0]*100:>+9.2f}%{vals[1]*100:>+9.2f}%{vals[2]*100:>+10.2f}%")

    base_mo, real_mo = rows[0][4], rows[1][4]
    cut = (1 - real_mo / base_mo) * 100 if base_mo else 0
    print(f"\n  [결론] 현실 슬리피지 반영 시 월수익 {rows[0][4]*100:+.2f}% → {rows[1][4]*100:+.2f}% "
          f"(약 {cut:.0f}% 잠식, 월 {(base_mo-real_mo)*CAP:,.0f}원 감소)")
    print(f"  ※ 새틀(잦은 단타)이 슬리피지에 가장 민감. 메이저 위주·과매매 자제가 방어책.")
    print(f"  ※ 새틀=국면 1구간 표본·AI게이트 제외(보수적). 코어=3년 실엔진. 과거≠미래.")


if __name__ == "__main__":
    main()
