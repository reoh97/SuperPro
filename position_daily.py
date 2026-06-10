"""중장기 추세추종 — 일봉 3년 검증 (돈키언 터틀식). 진짜 mid-long term.

  진입: 종가 > 200일선(장기 상승추세) AND 종가 ≥ 최근 50일 고점(돌파)
  청산: 종가 ≤ 최근 20일 저점(추세붕괴) OR 고점 − ATR×3(샹들리에)
  → 상승추세만 올라타고, 추세 꺾이면 빠져 현금(하락장 자동 회피, 숏 없이).

비교: '그냥 보유(buy&hold)' 대비 수익·낙폭(MDD). 추세추종은 보통 수익 비슷~약간↓, 낙폭 대폭↓.
사용법: python position_daily.py   (daily_data/ 필요)
"""
from __future__ import annotations
import glob, os, sys, numpy as np, pandas as pd

FEE = 0.0005
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def backtest(df, entry_n=50, exit_n=20, atr_k=3.0, ma_n=200):
    c = df["close"].to_numpy(float); h = df["high"].to_numpy(float); l = df["low"].to_numpy(float)
    ma = df["close"].rolling(ma_n).mean().to_numpy()
    dhi = df["high"].rolling(entry_n).max().shift(1).to_numpy()
    dlo = df["low"].rolling(exit_n).min().shift(1).to_numpy()
    a = atr(df).to_numpy()
    eq = 1.0; pos = None; trades = []; holds = []
    curve = []
    for i in range(ma_n+1, len(c)):
        if pos is not None:
            pos["peak"] = max(pos["peak"], h[i])
            stop = pos["peak"] - atr_k*a[i]
            if c[i] <= dlo[i] or c[i] <= stop:
                eq *= (c[i]/pos["entry"]) * (1-FEE)
                trades.append(c[i]/pos["entry"]-1); holds.append(i-pos["i0"]); pos = None
        elif c[i] > ma[i] and c[i] >= dhi[i]:
            pos = {"entry": c[i]*(1+FEE), "peak": h[i], "i0": i}
        cur = eq*(c[i]/pos["entry"]) if pos else eq
        curve.append(cur)
    if pos is not None:
        eq *= (c[-1]/pos["entry"])*(1-FEE); holds.append(len(c)-pos["i0"])
    curve = np.array(curve); peak = np.maximum.accumulate(curve)
    mdd = ((peak-curve)/peak).max()*100 if len(curve) else 0
    bh = c[-1]/c[ma_n+1]-1
    bh_curve = c[ma_n+1:]/c[ma_n+1]; bh_peak = np.maximum.accumulate(bh_curve)
    bh_mdd = ((bh_peak-bh_curve)/bh_peak).max()*100
    return (eq-1)*100, mdd, len(trades), (np.mean(holds) if holds else 0), bh*100, bh_mdd


def main():
    files = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_data", "*.csv")))
    if not files:
        print("daily_data/ 없음 — 집에서 fetch_daily.py 후 push"); return
    print("="*78)
    print("  중장기 추세추종(일봉 3년) vs 그냥 보유")
    print("="*78)
    print(f"  {'종목':<8}{'추세추종':>10}{'추세MDD':>9}{'거래':>5}{'평균보유':>8}{'  |':>3}{'그냥보유':>10}{'보유MDD':>9}")
    tr_rets, tr_mdds, bh_rets, bh_mdds = [], [], [], []
    for f in files:
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) < 250: continue
        r, mdd, n, hold, bh, bhmdd = backtest(df)
        tk = os.path.basename(f)[:-4].split("-")[1]
        tr_rets.append(r); tr_mdds.append(mdd); bh_rets.append(bh); bh_mdds.append(bhmdd)
        print(f"  {tk:<8}{r:>+9.0f}%{mdd:>8.0f}%{n:>5}{hold:>7.0f}일{'  |':>3}{bh:>+9.0f}%{bhmdd:>8.0f}%")
    print("  " + "-"*72)
    print(f"  {'평균':<8}{np.mean(tr_rets):>+9.0f}%{np.mean(tr_mdds):>8.0f}%{'':>5}{'':>8}{'  |':>3}"
          f"{np.mean(bh_rets):>+9.0f}%{np.mean(bh_mdds):>8.0f}%")
    print(f"\n  핵심: 추세추종이 '그냥 보유'보다 낙폭(MDD) 작으면 = 하락장 방어 성공.")
    print(f"        수익도 비슷하거나 높으면 = 중장기 엔진 채택 가치.")


if __name__ == "__main__":
    main()
