"""서킷브레이커 백테스트: 국면(상승/횡보/하락)별로 24시간 내 +목표% 도달 횟수.

라이브 서킷(trader/live.py)과 동일한 의미로 측정:
  - '총 평가액'(전 종목 현금+보유평가) 시계열을 만들고,
  - 기준선 대비 +target% 도달 시 1회 카운트하고 기준선을 그때 평가액으로 리셋
    (라이브의 '전량청산 후 재개=기준선 리셋'과 동일. 청산/재진입 수수료·타이밍은
     1봉 해상도로 근사 → 약간 낙관적일 수 있음. 빈도 추정용).
  - 그 도달 시점들을 24시간 창으로 묶어 '하루(24h)에 몇 번' 칠 수 있는지 집계.

사용법(시세 접근 가능한 환경에서):
    python circuit_backtest.py                 # bull/sideways/bear 모두
    python circuit_backtest.py bear            # 특정 국면만
    python circuit_backtest.py bull 3000 "2024-12-15 00:00:00"   # 구간 직접 지정
    python circuit_backtest.py --synthetic     # 합성데이터 스모크테스트(네트워크 불필요·설명용)

주의: 업비트 등 거래소 API가 막힌 환경(클라우드 등)에서는 실데이터를 받을 수 없습니다.
      그럴 땐 --synthetic 으로 동작만 확인하고, 실수치는 시세 접근 가능한 곳에서 받으세요.
"""
from __future__ import annotations

import copy
import os
import sys

import numpy as np
import pandas as pd
import yaml

from trader import backtest, portfolio

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 국면별 기본 구간(봉수, 종료시점 KST). 실제 달력에 맞게 인자로 덮어쓰기 가능.
REGIMES = {
    "bull":     {"count": 3000, "to_kst": "2024-12-15 00:00:00", "label": "상승장"},
    "sideways": {"count": 3000, "to_kst": "2024-10-01 00:00:00", "label": "횡보장"},
    "bear":     {"count": 6000, "to_kst": None,                  "label": "하락장(최신)"},
}


# ---------- 포트폴리오 평가액 시계열 ----------
def portfolio_equity(data: dict, cfg: dict) -> pd.Series:
    """전 종목을 시뮬레이션하고, 같은 시각으로 정렬·합산한 '총 평가액' 시계열을 만든다."""
    budget = float(cfg["portfolio"]["per_coin_krw"])
    cols = {}
    for tk, df in data.items():
        r = portfolio._run_coin(tk, df, cfg, record_curve=True)
        if not r.equity_curve:
            continue
        idx = pd.to_datetime([t for t, _ in r.equity_curve])
        cols[tk] = pd.Series([e for _, e in r.equity_curve], index=idx)
    if not cols:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(cols).sort_index()
    # 종목별로 진입 전 구간(NaN)은 그 종목 예산(현금)으로 채움 → 합이 항상 총예산 기준.
    frame = frame.ffill().fillna(budget)
    return frame.sum(axis=1)


# ---------- 서킷 리플레이 ----------
def count_hits(equity: pd.Series, base0: float, target_pct: float):
    """기준선 대비 +target% 도달 시마다 카운트(도달 직후 기준선=그때 평가액으로 리셋)."""
    baseline = base0
    thr = 1.0 + target_pct / 100.0
    hits = []
    for t, e in equity.items():
        if baseline > 0 and e >= baseline * thr:
            hits.append(pd.Timestamp(t))
            baseline = e
    return hits


def max_in_24h(hits) -> int:
    """임의의 연속 24시간 창에 들어가는 최대 도달 횟수."""
    best = 0
    for i, t0 in enumerate(hits):
        c = sum(1 for t in hits[i:] if t < t0 + pd.Timedelta(hours=24))
        best = max(best, c)
    return best


def analyze(equity: pd.Series, budget: float, target_pct: float, label: str) -> dict:
    if equity.empty:
        return {"label": label, "ok": False}
    start, end = equity.index[0], equity.index[-1]
    days = max((end - start).total_seconds() / 86400.0, 1e-9)
    hits = count_hits(equity, budget, target_pct)
    final_ret = (equity.iloc[-1] - budget) / budget * 100
    ttf = ((hits[0] - start).total_seconds() / 3600.0) if hits else None
    return {
        "label": label, "ok": True, "days": days, "bars": len(equity),
        "hits": len(hits), "per24h": len(hits) / days, "max24h": max_in_24h(hits),
        "final_ret": final_ret, "ttf_hours": ttf,
        "peak_ret": (equity.max() - budget) / budget * 100,
    }


# ---------- 합성데이터(네트워크 불필요, 스모크테스트/설명용) ----------
def synth_data(cfg: dict, regime_key: str, bars: int = 1500, seed: int = 0) -> dict:
    """국면별 드리프트/변동성으로 15분봉 합성. ⚠️ 실제 분포가 아님(동작확인용)."""
    rng = np.random.default_rng(seed)
    params = {  # (15분봉 드리프트, 변동성)
        "bull":     (0.0010, 0.006),
        "sideways": (0.0000, 0.004),
        "bear":     (-0.0008, 0.006),
    }[regime_key]
    drift, vol = params
    tickers = cfg["portfolio"]["tickers"]
    idx = pd.date_range("2025-01-01", periods=bars, freq="15min")
    data = {}
    for j, tk in enumerate(tickers):
        rets = rng.normal(drift, vol, bars)
        if regime_key == "sideways":  # 평균회귀(박스) 느낌
            rets = rets - 0.05 * np.cumsum(rets) / np.arange(1, bars + 1)
        close = 100.0 * np.exp(np.cumsum(rets))
        op = np.concatenate([[close[0]], close[:-1]])
        wig = np.abs(rng.normal(0, vol, bars))
        high = np.maximum(op, close) * (1 + wig)
        low = np.minimum(op, close) * (1 - wig)
        data[tk] = pd.DataFrame(
            {"open": op, "high": high, "low": low, "close": close,
             "volume": rng.uniform(1, 10, bars)}, index=idx)
    return data


# ---------- 메인 ----------
def load_real(cfg: dict, count: int, to_kst):
    unit = int(cfg.get("timeframe_min", 15))
    tickers = cfg["portfolio"]["tickers"]
    data = {}
    for tk in tickers:
        print(f"  [{tk}] {unit}분봉 {count}개 수집...", flush=True)
        df = backtest.fetch_minutes(tk, unit, count, to_kst=to_kst)
        if df is not None and len(df) >= 250:
            data[tk] = df
    return data


def main():
    args = [a for a in sys.argv[1:]]
    synthetic = "--synthetic" in args
    args = [a for a in args if a != "--synthetic"]

    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    target_pct = float(cfg.get("circuit", {}).get("profit_target_pct", 1.1))
    budget = float(cfg["portfolio"]["per_coin_krw"]) * len(cfg["portfolio"]["tickers"])

    # 어떤 국면을 돌릴지
    if args and args[0] in REGIMES:
        keys = [args[0]]
        if len(args) >= 2:
            REGIMES[args[0]]["count"] = int(args[1])
        if len(args) >= 3:
            REGIMES[args[0]]["to_kst"] = args[2]
    else:
        keys = ["bull", "sideways", "bear"]

    print("=" * 74)
    print(f"  서킷 백테스트 — 24시간 내 +{target_pct:g}% 도달 횟수  "
          f"(총자본 {budget:,.0f}원){'  [합성데이터]' if synthetic else ''}")
    print("=" * 74)

    rows = []
    for k in keys:
        meta = REGIMES[k]
        print(f"\n[{meta['label']}] 데이터 준비...", flush=True)
        if synthetic:
            data = synth_data(cfg, k)
        else:
            data = load_real(cfg, meta["count"], meta["to_kst"])
        if len(data) < 3:
            print(f"  데이터 부족({len(data)}종목) — 건너뜀. "
                  f"(거래소 API 차단 환경이면 --synthetic 사용)")
            continue
        eq = portfolio_equity(data, cfg)
        m = analyze(eq, budget, target_pct, meta["label"])
        rows.append(m)

    if not rows:
        print("\n결과 없음(데이터 미수집).")
        return

    print("\n" + "=" * 74)
    print(f"  {'국면':<12}{'기간(일)':>8}{'총도달':>7}{'24h평균':>9}{'24h최대':>9}{'최초도달':>10}{'기간수익':>9}")
    print("-" * 74)
    for m in rows:
        if not m["ok"]:
            continue
        ttf = f"{m['ttf_hours']:.1f}h" if m["ttf_hours"] is not None else "없음"
        print(f"  {m['label']:<12}{m['days']:>8.1f}{m['hits']:>7}{m['per24h']:>9.2f}"
              f"{m['max24h']:>9}{ttf:>10}{m['final_ret']:>+8.2f}%")
    print("=" * 74)
    print("  · 24h평균 = 총도달/기간일수, 24h최대 = 임의 연속 24시간 창의 최대 도달 횟수")
    print(f"  · 도달=기준선 대비 +{target_pct:g}%; 도달 즉시 기준선 리셋(라이브 재개와 동일)")
    if synthetic:
        print("  ⚠️ 합성데이터 결과는 '동작확인'용일 뿐 실제 빈도가 아님. 실수치는 실데이터로.")


if __name__ == "__main__":
    main()
