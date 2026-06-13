"""정확한 합산 백테스트 — 코어(일봉 실엔진) + 새틀라이트(연속 15분봉 실엔진), 75/25.

random_backtest.py 는 새틀을 '국면조건부 근사'로 합성했지만, 이건 min15_data/ 의
연속 15분봉에 **실제 새틀 엔진(trader.backtest.run)** 을 돌려 정확히 측정한다.
코어·새틀을 같은 일자 타임라인으로 정렬해 0.75·코어 + 0.25·새틀 로 합산.

전제:
  - min15_data/<티커>.csv (연속 15분봉) 필요 → 없으면 fetch_15m.py 를 한국 IP에서 먼저 실행.
  - 코어: daily_data/ (이미 있음).
  - ⚠️ AI 장세게이트는 과거 뉴스가 없어 백테스트 불가 → 새틀은 '차트 전용' 엔진으로 측정.
    라이브의 AI BEAR 방어가 빠진 값이라, 실제 약세장 성과는 이 결과보다 '더 나을' 수 있음(보수적).

사용법:  python backtest_full.py
"""
from __future__ import annotations
import glob, os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, yaml

import ratio_optimize as R           # core_curve, sat_window 재사용(검증된 실엔진 경로)
from trader import backtest as sat_bt

BASE = os.path.dirname(os.path.abspath(__file__))
MIN15 = os.path.join(BASE, "min15_data")
CAP = 20_000_000
W_CORE, W_SAT = 0.75, 0.25
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def core_daily(cfg):
    """코어 실엔진 일수익(동일가중, 전체기간)."""
    lt = cfg["longterm"]; curves = []
    for f in sorted(glob.glob(os.path.join(BASE, "daily_data", "*.csv"))):
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) >= lt["ma_n"] + 30:
            curves.append(R.core_curve(df, lt))
    mat = pd.concat(curves, axis=1, sort=True).ffill().dropna()
    return mat.pct_change().mean(axis=1).dropna()


def sat_daily(cfg):
    """새틀 실엔진(연속 15분봉) 일수익(동일가중). min15_data/ 필요."""
    data = {}
    for f in sorted(glob.glob(os.path.join(MIN15, "*.csv"))):
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) > 300:
            data[os.path.basename(f)[:-4]] = df
    if not data:
        return None, 0
    dr, _ = R.sat_window(data, cfg)      # 실제 backtest.run 기반 일수익
    return dr, len(data)


def report(name, daily, n_months):
    eq = (1 + daily).cumprod()
    tot = eq.iloc[-1] - 1
    mdd = ((eq.cummax() - eq) / eq.cummax()).max()
    mo = (1 + tot) ** (1 / n_months) - 1 if n_months > 0 else 0
    print(f"  {name:<12} 총 {tot*100:+7.1f}%  월 {mo*100:+5.2f}%/월  "
          f"({mo*CAP:+,.0f}원/월)  MDD {mdd*100:4.1f}%")
    return tot, mo, mdd


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    sd, n = sat_daily(cfg)
    if sd is None:
        print("=" * 68)
        print("  min15_data/ 가 없습니다 — 연속 15분봉을 먼저 받아야 정확 백테스트 가능.")
        print("  1) 한국 IP PC/폰에서:  python fetch_15m.py")
        print("  2) git add min15_data && git commit -m '연속 15분봉' && git push")
        print("  3) 여기서:  python backtest_full.py")
        print("=" * 68)
        return

    cd = core_daily(cfg)
    # 공통 일자 정렬(겹치는 기간만)
    idx = cd.index.intersection(sd.index)
    if len(idx) < 30:
        print(f"코어·새틀 겹치는 기간이 너무 짧습니다({len(idx)}일). 15분봉 기간을 늘리세요."); return
    cd, sd = cd.reindex(idx).fillna(0), sd.reindex(idx).fillna(0)
    comb = W_CORE * cd + W_SAT * sd
    n_months = len(idx) / 30.0

    # 그냥보유(같은 기간)
    bh = []
    for f in sorted(glob.glob(os.path.join(BASE, "daily_data", "*.csv"))):
        c = pd.read_csv(f, index_col=0, parse_dates=True)["close"].reindex(idx).ffill()
        bh.append(c / c.iloc[0])
    bhd = pd.concat(bh, axis=1, sort=True).ffill().pct_change().mean(axis=1).fillna(0)

    print("=" * 68)
    print(f"  정확한 합산 백테스트 — 코어(일봉 실엔진)+새틀({n}종목 15분봉 실엔진)")
    print("=" * 68)
    print(f"  기간 {idx[0].date()}~{idx[-1].date()} ({n_months:.1f}개월) · 2,000만원 기준")
    print()
    report("🪨 코어단독", cd, n_months)
    report("🛰 새틀단독", sd, n_months)
    report("⚖ 합산75/25", comb, n_months)
    report("📉 그냥보유", bhd, n_months)
    print()
    print("  ※ 새틀=차트전용 실엔진(AI 뉴스게이트 제외 → 약세장 보수적). 과거≠미래.")


if __name__ == "__main__":
    main()
