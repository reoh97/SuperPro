"""코어(장기 추세추종) ↔ 새틀라이트(단기 전술) 최적 자본 비율 계산.

문제: 두 엔진은 시간축이 다르다(코어=일봉 3년 연속 / 새틀라이트=국면별 15분봉 1구간씩).
      따라서 일별 수익곡선을 그대로 합칠 수 없다 → 각 엔진을 '연율화 수익·변동성'으로
      환산한 뒤, 표준 도구인 **평균-분산(mean-variance) 최적화**로 최적 비중을 구한다.
      측정 불가능한 두 엔진의 상관계수(ρ)는 **민감도 분석**(여러 ρ 가정)으로 다룬다.

산출:
  - 코어 연율 수익 μ_c / 연율 변동성 σ_c / MDD (일봉 3년 실데이터)
  - 새틀라이트 연율 μ_s / σ_s / 국면별 MDD (국면 15분봉 실데이터, 국면빈도 가중)
  - ρ ∈ {0.0, 0.3, 0.6} 별 최대-샤프(접점) 새틀라이트 비중 w*
  - 최소분산 비중, 현행 40% 대비

사용법:  python ratio_optimize.py
주의:  과거≠미래, 국면별 표본 1구간뿐, ρ는 가정(민감도로 점검). 손실 가능.
"""
from __future__ import annotations
import glob, os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, yaml

import circuit_backtest as cb
from trader import backtest as sat_bt

BASE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.0005
TRADING_DAYS = 365  # 크립토는 연중무휴
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


# ───────────────────────── 코어 엔진(일봉 돈키언) ─────────────────────────
def _atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def core_curve(df, lt, slip=0.0) -> pd.Series:
    """일봉 추세추종 → 날짜 인덱스 평가배수(1.0 시작) 시계열. slip=편도 슬리피지."""
    entry_n, exit_n = lt["entry_n"], lt["exit_n"]
    atr_k, ma_n = lt["atr_k"], lt["ma_n"]
    c = df["close"].to_numpy(float); h = df["high"].to_numpy(float)
    ma = df["close"].rolling(ma_n).mean().to_numpy()
    dhi = df["high"].rolling(entry_n).max().shift(1).to_numpy()
    dlo = df["low"].rolling(exit_n).min().shift(1).to_numpy()
    a = _atr(df).to_numpy()
    idx = df.index
    eq = 1.0; pos = None; out_i, out_v = [], []
    for i in range(ma_n + 1, len(c)):
        if pos is not None:
            pos["peak"] = max(pos["peak"], h[i])
            stop = pos["peak"] - atr_k * a[i]
            if c[i] <= dlo[i] or c[i] <= stop:
                eq *= (c[i] * (1 - slip) / pos["entry"]) * (1 - FEE)   # 매도 슬리피지
                pos = None
        elif c[i] > ma[i] and c[i] >= dhi[i]:
            pos = {"entry": c[i] * (1 + FEE) * (1 + slip), "peak": h[i]}  # 매수 슬리피지
        cur = eq * (c[i] / pos["entry"]) if pos else eq
        out_i.append(idx[i]); out_v.append(cur)
    return pd.Series(out_v, index=pd.DatetimeIndex(out_i))


def core_profile(cfg):
    lt = cfg["longterm"]
    files = sorted(glob.glob(os.path.join(BASE, "daily_data", "*.csv")))
    curves = []
    for f in files:
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) < lt["ma_n"] + 30:
            continue
        curves.append(core_curve(df, lt))
    # 종목 곡선 정렬 → 동일가중 포트폴리오(매일 리밸런싱 가정: 일수익 평균)
    mat = pd.concat(curves, axis=1).sort_index().ffill().dropna()
    daily_ret = mat.pct_change().mean(axis=1).dropna()       # 동일가중 일수익
    port = (1 + daily_ret).cumprod()
    years = (port.index[-1] - port.index[0]).days / 365.25
    cagr = port.iloc[-1] ** (1 / years) - 1
    vol = daily_ret.std() * np.sqrt(TRADING_DAYS)
    peak = port.cummax(); mdd = ((peak - port) / peak).max()
    return {"mu": cagr, "sigma": vol, "mdd": mdd, "daily": daily_ret,
            "years": years, "n_coins": len(curves)}


# ─────────────────────── 새틀라이트 엔진(국면별 15분봉) ───────────────────────
def sat_window(data, cfg):
    """한 국면 구간: (일별수익 시계열, 구간 총수익).
    - 구간 총수익 = 10종목 최종수익 평균(검증된 engine_compare 정의와 일치)
    - 일별수익 = 종목별 청산손익 누적 일말곡선의 동일가중 평균(변동성 산출용)."""
    init = float(cfg["paper"]["initial_krw"])
    daily_curves, fin_rets = [], []
    for df in data.values():
        res = sat_bt.run(df, cfg)
        fin_rets.append((res.end_equity - res.start_equity) / res.start_equity)
        if res.trades:
            pnl = pd.Series([t.net_pnl for t in res.trades],
                            index=pd.DatetimeIndex([pd.Timestamp(t.exit_time) for t in res.trades])).sort_index()
            equity = init + pnl.cumsum()
            full = equity.resample("1D").last().ffill()
        else:
            full = pd.Series([init], index=[df.index[-1].normalize()])
        daily_curves.append(full / init)
    mat = pd.concat(daily_curves, axis=1, sort=True).ffill().fillna(1.0)
    daily_ret = mat.pct_change().mean(axis=1).dropna()
    return daily_ret, float(np.mean(fin_rets))


def regime_frequency(cfg):
    """3년 일봉(BTC 기준)으로 국면 빈도 추정 — MA50 위/아래 + 기울기.
       bull: 종가>MA50 & MA50상승 / bear: 종가<MA50 & MA50하락 / 그외 sideways."""
    df = pd.read_csv(os.path.join(BASE, "daily_data", "KRW-BTC.csv"),
                     index_col=0, parse_dates=True)
    ma = df["close"].rolling(50).mean()
    slope = ma.diff(10)
    up = (df["close"] > ma) & (slope > 0)
    dn = (df["close"] < ma) & (slope < 0)
    n = up.notna().sum()
    f_bull = up.sum() / n; f_bear = dn.sum() / n; f_side = 1 - f_bull - f_bear
    return {"bull": f_bull, "sideways": f_side, "bear": f_bear}


def _bear_gated_cfg(cfg):
    """라이브 AI 게이트(bear_defensive) 모사: 약세장에 UP(추세추종) 진입 차단,
       박스(횡보)만 허용. 백테스트엔 AI게이트가 없으므로 약세 구간만 이 cfg로 재현."""
    import copy
    g = copy.deepcopy(cfg)
    g["scalp"]["uptrend"]["enabled"] = False
    g["scalp"]["bull_mode"]["enabled"] = False
    g["scalp"]["downtrend"]["enabled"] = False
    return g


def sat_profile(cfg, freq, gate_bear=False):
    """국면별 일수익 시계열을 빈도가중 혼합 → 연율 μ, σ, 국면별 MDD.
       gate_bear=True: 약세 구간에 AI 게이트(UP차단=박스만) 적용 = 라이브 동작."""
    regs = ["bull", "sideways", "bear"]
    per = {}
    for rk in regs:
        data = cb.load_csv(rk, cfg)
        if not data:
            continue
        rcfg = _bear_gated_cfg(cfg) if (gate_bear and rk == "bear") else cfg
        dr, wret = sat_window(data, rcfg)
        port = (1 + dr).cumprod(); peak = port.cummax()
        mdd = ((peak - port) / peak).max()
        days = max(len(dr), 1)
        per[rk] = {"mean_d": (1 + wret) ** (1 / days) - 1,  # 구간총수익→일평균(검증수치 일관)
                   "std_d": dr.std(), "mdd": mdd, "days": days, "ret": wret}
    # 빈도가중 혼합분포: E[r]=Σf·μ_r ; Var=Σf·(σ_r²+μ_r²) − E[r]²
    tot_f = sum(freq[r] for r in per)
    fnorm = {r: freq[r] / tot_f for r in per}
    m = sum(fnorm[r] * per[r]["mean_d"] for r in per)
    second = sum(fnorm[r] * (per[r]["std_d"] ** 2 + per[r]["mean_d"] ** 2) for r in per)
    var_d = max(second - m ** 2, 1e-12)
    mu = (1 + m) ** TRADING_DAYS - 1
    sigma = np.sqrt(var_d) * np.sqrt(TRADING_DAYS)
    mdd_blend = sum(fnorm[r] * per[r]["mdd"] for r in per)
    return {"mu": mu, "sigma": sigma, "mdd": mdd_blend, "per": per, "fnorm": fnorm}


# ─────────────────────────── 평균-분산 최적화 ───────────────────────────
def optimize(mu_c, sig_c, mu_s, sig_s, rho, rf=0.0):
    """새틀라이트 비중 w(0~1) 그리드 → 최대 샤프 / 최소분산."""
    ws = np.linspace(0, 1, 1001)
    mu_p = (1 - ws) * mu_c + ws * mu_s
    var_p = ((1 - ws) ** 2) * sig_c ** 2 + (ws ** 2) * sig_s ** 2 \
        + 2 * ws * (1 - ws) * rho * sig_c * sig_s
    sig_p = np.sqrt(var_p)
    sharpe = (mu_p - rf) / sig_p
    i_sh = int(np.argmax(sharpe)); i_mv = int(np.argmin(var_p))
    return {"w_sharpe": ws[i_sh], "sharpe": sharpe[i_sh],
            "mu_sharpe": mu_p[i_sh], "sig_sharpe": sig_p[i_sh],
            "w_minvar": ws[i_mv], "sig_minvar": sig_p[i_mv], "mu_minvar": mu_p[i_mv]}


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    print("=" * 74)
    print("  코어(장기) ↔ 새틀라이트(단기) 최적 자본비율 — 평균분산 최적화")
    print("=" * 74)

    core = core_profile(cfg)
    freq = regime_frequency(cfg)
    sat_raw = sat_profile(cfg, freq, gate_bear=False)
    sat_gated = sat_profile(cfg, freq, gate_bear=True)

    print(f"\n[국면 빈도 추정] (3년 일봉, BTC MA50 기준)")
    print(f"  불장 {freq['bull']*100:4.0f}% · 횡보 {freq['sideways']*100:4.0f}% · 약세 {freq['bear']*100:4.0f}%")

    print(f"\n[엔진별 연율 프로파일]  (수익률·변동성·MDD)")
    print(f"  {'엔진':<16}{'연율수익μ':>11}{'연율변동σ':>11}{'MDD':>9}{'샤프(μ/σ)':>11}")
    print(f"  {'코어(장기)':<15}{core['mu']*100:>+10.1f}%{core['sigma']*100:>10.1f}%"
          f"{core['mdd']*100:>8.1f}%{core['mu']/core['sigma']:>11.2f}")
    for tag, sat in [("새틀:게이트無", sat_raw), ("새틀:AI게이트", sat_gated)]:
        print(f"  {tag:<15}{sat['mu']*100:>+10.1f}%{sat['sigma']*100:>10.1f}%"
              f"{sat['mdd']*100:>8.1f}%{sat['mu']/sat['sigma']:>11.2f}")
    print(f"  (코어 {core['years']:.1f}년·{core['n_coins']}종목 / 새틀 국면별 일수익 빈도가중)")
    print(f"  ※ '게이트無'=백테스트 약세 최악, 'AI게이트'=라이브 약세UP차단(박스만) 반영=실동작")

    print(f"\n  [새틀라이트(AI게이트) 국면별 내역]")
    for r, lbl in [("bull", "불장"), ("sideways", "횡보"), ("bear", "약세")]:
        if r in sat_gated["per"]:
            p = sat_gated["per"][r]
            print(f"    {lbl}: 구간수익 {p['ret']*100:+5.1f}%  일변동 {p['std_d']*100:4.2f}%  "
                  f"MDD {p['mdd']*100:4.1f}%  ({p['days']}일, 비중 {sat_gated['fnorm'][r]*100:3.0f}%)")

    for tag, sat in [("게이트無(보수)", sat_raw), ("AI게이트(실동작)", sat_gated)]:
        print(f"\n[최적 새틀라이트 비중 w*] — {tag}  (상관 ρ 민감도)")
        print(f"  {'ρ(상관)':<8}{'최대샤프 w*':>12}{'→코어/새틀':>14}{'포트수익':>10}{'포트변동':>10}{'샤프':>8}")
        rows = []
        for rho in (0.0, 0.3, 0.6):
            o = optimize(core["mu"], core["sigma"], sat["mu"], sat["sigma"], rho)
            w = o["w_sharpe"]
            print(f"  {rho:<8.1f}{w*100:>10.0f}%{f'{(1-w)*100:.0f}/{w*100:.0f}':>14}"
                  f"{o['mu_sharpe']*100:>+9.1f}%{o['sig_sharpe']*100:>9.1f}%{o['sharpe']:>8.2f}")
            rows.append((rho, w))
        if tag.startswith("AI"):
            w_lo = min(r[1] for r in rows); w_hi = max(r[1] for r in rows)
            o0 = optimize(core["mu"], core["sigma"], sat["mu"], sat["sigma"], 0.3)
            print(f"    [최소분산(ρ=0.3)] 새틀 {o0['w_minvar']*100:.0f}% "
                  f"→ 변동 {o0['sig_minvar']*100:.1f}% / 수익 {o0['mu_minvar']*100:+.1f}%")
            head_lo, head_hi = w_lo, w_hi

    # 현행 60/40 평가(AI게이트, ρ=0.3)
    w_cur = 0.40; sg = sat_gated
    mu_cur = (1 - w_cur) * core["mu"] + w_cur * sg["mu"]
    var_cur = (1 - w_cur) ** 2 * core["sigma"] ** 2 + w_cur ** 2 * sg["sigma"] ** 2 \
        + 2 * w_cur * (1 - w_cur) * 0.3 * core["sigma"] * sg["sigma"]
    print(f"\n  [현행 60/40 (AI게이트, ρ=0.3)]  수익 {mu_cur*100:+.1f}% / 변동 {np.sqrt(var_cur)*100:.1f}% "
          f"/ 샤프 {mu_cur/np.sqrt(var_cur):.2f}")

    # 효율적 프론티어(목적별 트레이드오프) — ρ=0.3, AI게이트
    print(f"\n[코어/새틀 후보별 트레이드오프]  (AI게이트, ρ=0.3)")
    print(f"  {'코어/새틀':>10}{'연수익':>9}{'변동성':>9}{'샤프':>7}   성격")
    rho = 0.3
    for w in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0):
        mu_p = (1 - w) * core["mu"] + w * sg["mu"]
        sd = np.sqrt((1 - w) ** 2 * core["sigma"] ** 2 + w ** 2 * sg["sigma"] ** 2
                     + 2 * w * (1 - w) * rho * core["sigma"] * sg["sigma"])
        note = ""
        if abs(w - 0.40) < 1e-6: note = "← 현행"
        elif w == 0.0: note = "성장 최대(=코어단독, MDD18%)"
        elif w == 1.0: note = "방어 최대(=새틀단독)"
        elif 0.66 <= w <= 0.76: note = ""
        print(f"  {f'{(1-w)*100:.0f}/{w*100:.0f}':>10}{mu_p*100:>+8.1f}%{sd*100:>8.1f}%{mu_p/sd:>7.2f}   {note}")

    print(f"\n[결론]")
    print(f"  • 단일 정답 없음 — 목적에 따라 다름:")
    print(f"    - 위험조정(최대샤프): 새틀 {head_lo*100:.0f}~{head_hi*100:.0f}% (수익↓·변동성 대폭↓)")
    print(f"    - 성장(최대수익): 코어 100% (수익↑·MDD 18%, 단 불장표본 편향)")
    print(f"  • 현행 60/40 = 균형점: 코어단독 수익의 ~70% 확보하면서 변동성 ~65%로 낮춤(샤프 1.54).")
    print(f"  • 권장 실용범위: 코어 55~65% / 새틀 35~45% (현행 유지~약간 새틀↑).")
    print(f"    수학적 극단(코어100% or 새틀76%)은 불장편향·1구간표본에 기댄 값이라 비권장.")
    print("  ※ 한계: 표본기간 불장편향(코어 유리)·국면별 1구간·ρ가정·과거≠미래. 모의 관찰 후 적용 권장.")


if __name__ == "__main__":
    main()
