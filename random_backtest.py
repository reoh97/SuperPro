"""무작위 기간·무작위 날짜 백테스트 (몬테카를로 로버스트니스).

고정 국면구간만 보면 체리피킹/과적합 위험 → 임의 시작일·임의 길이의 윈도우 N개를 뽑아
시스템(코어75 + 새틀25)을 돌리고 '결과 분포'(중앙값/하위10%/최악/양수비율)를 본다.

데이터 현실:
  - 코어(장기, 일봉 3년 연속): 실제 엔진(돈키언)으로 각 무작위 윈도우 정확 백테스트.
    (윈도우 시작 전 200일은 MA200 워밍업으로 포함 → 진입판단 정상)
  - 새틀(단기, 15분봉): 연속데이터 없음(국면별 3구간뿐) → 검증된 '국면조건부 일수익
    프로파일'(ratio_optimize, AI게이트)로 일자별 국면을 분류해 근사 합성.
  → 코어는 정확, 새틀은 근사. 합산은 일별 0.75·코어 + 0.25·새틀.

사용법:  python random_backtest.py [샘플수] [최소일] [최대일] [시드]
  예)    python random_backtest.py 300 120 365 0
주의: 과거≠미래. 새틀 근사는 국면 1구간 표본 기반. 분포의 '하단(최악)'을 특히 볼 것.
"""
from __future__ import annotations
import glob, os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, yaml

import ratio_optimize as R   # core_curve, sat_profile, _atr 재사용(일관성)

BASE = os.path.dirname(os.path.abspath(__file__))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

W_CORE, W_SAT = 0.75, 0.25   # 채택 비율(config 75/25)


def load_daily():
    out = {}
    for f in sorted(glob.glob(os.path.join(BASE, "daily_data", "*.csv"))):
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) > 260:
            out[os.path.basename(f)[:-4]] = df
    return out


def classify_daily(df) -> pd.Series:
    """일자별 시장국면: bull(종가>MA50&상승) / bear(종가<MA50&하락) / sideways."""
    ma = df["close"].rolling(50).mean()
    slope = ma.diff(10)
    reg = pd.Series("sideways", index=df.index)
    reg[(df["close"] > ma) & (slope > 0)] = "bull"
    reg[(df["close"] < ma) & (slope < 0)] = "bear"
    return reg


def core_window_daily(data, lt, s_idx, e_idx, ref_index):
    """무작위 윈도우 [s,e]의 코어 일수익(동일가중). 워밍업 위해 df[:e]를 먹이고 윈도우만 슬라이스."""
    curves = []
    win = ref_index[s_idx:e_idx]
    for df in data.values():
        if e_idx > len(df):
            continue
        cur = R.core_curve(df.iloc[:e_idx], lt)          # 전체 히스토리로 MA200 워밍업
        cur = cur.reindex(win).ffill().dropna()
        if len(cur) > 5:
            curves.append(cur / cur.iloc[0])             # 윈도우 시작=1로 재정규화
    if not curves:
        return None
    mat = pd.concat(curves, axis=1, sort=True).ffill().dropna()
    return mat.pct_change().mean(axis=1).dropna()        # 동일가중 일수익


def bh_window_daily(data, s_idx, e_idx, ref_index):
    win = ref_index[s_idx:e_idx]
    curves = []
    for df in data.values():
        c = df["close"].reindex(win).ffill().dropna()
        if len(c) > 5:
            curves.append(c / c.iloc[0])
    mat = pd.concat(curves, axis=1, sort=True).ffill().dropna()
    return mat.pct_change().mean(axis=1).dropna()


def stats(daily: pd.Series):
    if daily is None or len(daily) < 5:
        return None
    eq = (1 + daily).cumprod()
    ret = eq.iloc[-1] - 1
    peak = eq.cummax(); mdd = ((peak - eq) / peak).max()
    return {"ret": ret, "mdd": mdd, "days": len(daily)}


def dist(vals):
    a = np.array(vals) * 100
    return (f"중앙 {np.median(a):+5.1f}%  하위10% {np.percentile(a,10):+5.1f}%  "
            f"최악 {a.min():+5.1f}%  최고 {a.max():+5.1f}%  양수 {(a>0).mean()*100:3.0f}%")


def main():
    args = sys.argv[1:]
    N = int(args[0]) if len(args) > 0 else 300
    LMIN = int(args[1]) if len(args) > 1 else 120
    LMAX = int(args[2]) if len(args) > 2 else 365
    seed = int(args[3]) if len(args) > 3 else 0
    rng = np.random.default_rng(seed)

    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    lt = cfg["longterm"]
    data = load_daily()
    # 공통 일자 인덱스(BTC 기준) — 모든 코인 정렬용
    ref = data["KRW-BTC"]
    ref_index = ref.index
    regs = classify_daily(ref)

    # 새틀 국면조건부 일수익 프로파일(AI게이트) — 한 번 계산해 재사용
    freq = R.regime_frequency(cfg)
    sat = R.sat_profile(cfg, freq, gate_bear=True)["per"]   # {rk:{mean_d,std_d,...}}
    sat_d = {r: (sat[r]["mean_d"], sat[r]["std_d"]) for r in sat}

    ma_n = lt["ma_n"]
    lo = ma_n + 10                       # 워밍업 확보 위해 이 인덱스 이후에서만 시작
    hi = len(ref_index) - LMIN - 1
    if hi <= lo:
        print("데이터가 짧아 무작위 윈도우를 못 만듭니다."); return

    core_r, core_m, comb_r, comb_m, sat_r, bh_r = [], [], [], [], [], []
    comb_days = []
    used = 0
    for _ in range(N):
        s_idx = int(rng.integers(lo, hi))
        L = int(rng.integers(LMIN, LMAX + 1))
        e_idx = min(s_idx + L, len(ref_index))
        if e_idx - s_idx < LMIN:
            continue
        cd = core_window_daily(data, lt, s_idx, e_idx, ref_index)
        if cd is None:
            continue
        # 새틀 근사: 윈도우 각 일자의 국면 → 해당 국면 일수익 추출(정규분포 표본)
        win_regs = regs.reindex(ref_index[s_idx:e_idx]).fillna("sideways")
        win_regs = win_regs.reindex(cd.index).fillna("sideways")
        sd = np.array([rng.normal(*sat_d.get(r, sat_d["sideways"])) for r in win_regs])
        sat_daily = pd.Series(sd, index=cd.index)
        comb_daily = W_CORE * cd + W_SAT * sat_daily
        bh = bh_window_daily(data, s_idx, e_idx, ref_index).reindex(cd.index).fillna(0)

        sc, ss, scb, sbh = stats(cd), stats(sat_daily), stats(comb_daily), stats(bh)
        if not sc:
            continue
        core_r.append(sc["ret"]); core_m.append(sc["mdd"])
        sat_r.append(ss["ret"])
        comb_r.append(scb["ret"]); comb_m.append(scb["mdd"]); comb_days.append(scb["days"])
        bh_r.append(sbh["ret"])
        used += 1

    print("=" * 80)
    print(f"  무작위 기간 백테스트 — {used}개 윈도우 (길이 {LMIN}~{LMAX}일, 시드 {seed})")
    print("=" * 80)
    print(f"  코인 {len(data)}개 · 일봉 {ref_index[0].date()}~{ref_index[-1].date()} 중 임의 구간 추출")
    print(f"\n  [수익률 분포]  (윈도우당 총수익, 비연율)")
    print(f"  🪨 코어단독   {dist(core_r)}")
    print(f"  🛰 새틀단독*  {dist(sat_r)}")
    print(f"  ⚖ 합산 75/25 {dist(comb_r)}")
    print(f"  📉 그냥보유   {dist(bh_r)}")
    print(f"\n  [최대낙폭(MDD) 분포]  (작을수록 안전)")
    print(f"  🪨 코어단독   중앙 {np.median(core_m)*100:4.1f}%  최악 {np.max(core_m)*100:4.1f}%")
    print(f"  ⚖ 합산 75/25 중앙 {np.median(comb_m)*100:4.1f}%  최악 {np.max(comb_m)*100:4.1f}%")
    bh = np.array(bh_r)*100; cb = np.array(comb_r)*100
    print(f"\n  [해석]")
    print(f"  • 합산 양수 윈도우 비율 {(np.array(comb_r)>0).mean()*100:.0f}% "
          f"(그냥보유 {(bh>0).mean():.0%}) — 무작위 진입에서도 이기는 빈도")
    print(f"  • 합산이 그냥보유 이긴 윈도우 {(cb>bh).mean()*100:.0f}%")
    # ---- 2,000만원 기준 금액 + 월 환산 ----
    CAP = 20_000_000
    cr = np.array(comb_r); cd = np.array(comb_days)
    monthly = (1 + cr) ** (30.0 / cd) - 1            # 윈도우별 '한 달 환산' 수익률
    print(f"\n  [2,000만원 투자 시 — 합산 75/25]")
    print(f"  • 윈도우 전체기간 수익(평균 {cd.mean()/30:.1f}개월): "
          f"보통(중앙) {np.median(cr)*100:+.1f}% = {np.median(cr)*CAP:+,.0f}원  / "
          f"평균 {cr.mean()*100:+.1f}% = {cr.mean()*CAP:+,.0f}원")
    print(f"  • 월 환산 수익률: 보통(중앙) {np.median(monthly)*100:+.2f}%/월  / "
          f"평균 {monthly.mean()*100:+.2f}%/월")
    print(f"  • 월 환산 금액(2천만): 보통 {np.median(monthly)*CAP:+,.0f}원/월  / "
          f"평균 {monthly.mean()*CAP:+,.0f}원/월")
    print(f"  • 월수익 분포: 하위10% {np.percentile(monthly,10)*100:+.2f}%/월 "
          f"({np.percentile(monthly,10)*CAP:+,.0f}원) · 상위10% {np.percentile(monthly,90)*100:+.2f}%/월 "
          f"({np.percentile(monthly,90)*CAP:+,.0f}원)")
    print(f"  ※ *새틀은 국면조건부 근사(연속 15분봉 부재). 코어는 실엔진 정확. 과거≠미래.")


if __name__ == "__main__":
    main()
