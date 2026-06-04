"""서킷 백테스트 — 실데이터 '프록시'(GitHub 호스팅 BTC/USD 1분봉).

업비트 KRW 분봉은 업비트가 해외/클라우드 IP를 지역차단(403)해서 이 환경에선 못 받는다.
그래서 거래소가 아닌 **GitHub에 공개된 BTC/USD 1분봉**(Bitstamp, 2012~2025)을 받아
15분봉으로 바꾼 뒤, 실제 상승/횡보/하락 구간에서 '24h 내 +목표% 도달 횟수'를 측정한다.

⚠️ 한계(정직):
  - **BTC/USD 단일자산 풀투입**이라 KRW 10종목 분산 포트폴리오와 다르다(빈도 과대평가 경향).
  - 전략 파라미터는 KRW 메이저용 → BTC/USD엔 덜 맞아 수익 자체는 과소평가될 수 있다.
  - 그래도 '전략 평가액이 하루에 +1.1% 만큼 출렁이는가'라는 구조적 결론은 견고하다.
  정확한 KRW 수치는 fetch_local.py 를 업비트 접근 가능한 곳에서 돌려 backtest_data/ 를 커밋할 것.

사용법:
    python proxy_backtest.py            # 1.1% 고정 + 목표% 스윕
데이터: data/btcusd_1min.csv.gz (없으면 GitHub raw 에서 자동 다운로드 → data/ 는 gitignore)
"""
from __future__ import annotations

import copy
import os
import sys
import urllib.request

import pandas as pd
import yaml

import circuit_backtest as cb

BASE = os.path.dirname(os.path.abspath(__file__))
GZ = os.path.join(BASE, "data", "btcusd_1min.csv.gz")
URL = ("https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/"
       "main/data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz")

# 실제 BTC 국면 구간(USD 기준, 잘 알려진 시기). 필요시 수정.
WINDOWS = {
    "상승장(23-10~24-03)": ("2023-10-01", "2024-03-10"),
    "횡보장(23-08~23-09)": ("2023-07-20", "2023-09-20"),
    "하락장(22-04~22-06)": ("2022-04-01", "2022-06-30"),
}
TARGETS = [0.1, 0.2, 0.3, 0.5, 0.8, 1.1]
BUDGET = 20_000_000

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_15m() -> pd.DataFrame:
    if not os.path.exists(GZ):
        os.makedirs(os.path.dirname(GZ), exist_ok=True)
        print(f"BTC 1분봉 다운로드(약 90MB) ← GitHub ...", flush=True)
        urllib.request.urlretrieve(URL, GZ)
    df = pd.read_csv(GZ)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.set_index("dt").sort_index()
    return pd.DataFrame({
        "open": df["open"].resample("15min").first(),
        "high": df["high"].resample("15min").max(),
        "low": df["low"].resample("15min").min(),
        "close": df["close"].resample("15min").last(),
        "volume": df["volume"].resample("15min").sum(),
    }).dropna()


def main():
    m15 = load_15m()
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    cfg = copy.deepcopy(cfg)
    cfg["portfolio"]["tickers"] = ["BTCUSD"]        # 단일자산 풀투입
    cfg["portfolio"]["per_coin_krw"] = BUDGET
    target0 = float(cfg.get("circuit", {}).get("profit_target_pct", 1.1))

    eqs = {}
    print("\n구간별 BTC 가격변화(국면 확인):")
    for label, (s, e) in WINDOWS.items():
        sl = m15.loc[s:e].copy()
        chg = (sl["close"].iloc[-1] / sl["close"].iloc[0] - 1) * 100
        eqs[label] = cb.portfolio_equity({"BTCUSD": sl}, cfg)
        peak = (eqs[label].max() / BUDGET - 1) * 100
        print(f"  {label:<22} {len(sl):>6}봉  BTC {chg:>+7.1f}%  | 전략 평가액 최고 {peak:+.2f}%")

    print("\n" + "=" * 74)
    print(f"  [실데이터·프록시 BTC/USD] 목표 +{target0:g}% 24h 도달  (총자본 {BUDGET:,})")
    print("=" * 74)
    print(f"  {'국면':<22}{'일수':>7}{'총도달':>8}{'24h평균':>9}{'24h최대':>9}{'기간수익':>10}")
    print("-" * 74)
    for label in WINDOWS:
        m = cb.analyze(eqs[label], BUDGET, target0, label)
        print(f"  {label:<22}{m['days']:>7.1f}{m['hits']:>8}{m['per24h']:>9.2f}"
              f"{m['max24h']:>9}{m['final_ret']:>+9.2f}%")
    print("=" * 74)

    print(f"\n  목표%별 24h당 도달(평균, 괄호=임의24h창 최대) — 어느 목표라야 발동되나:")
    print(f"  {'목표%':>6} | " + " | ".join(f"{l:>20}" for l in WINDOWS))
    print("  " + "-" * 70)
    for tp in TARGETS:
        cells = []
        for label in WINDOWS:
            m = cb.analyze(eqs[label], BUDGET, tp, label)
            cells.append(f"{m['per24h']:>6.2f} ({m['max24h']:>2})")
        print(f"  {tp:>5.1f}% | " + " | ".join(f"{c:>20}" for c in cells))
    print("\n  ⚠️ BTC/USD 단일 풀투입 프록시 — 빈도 과대평가 경향. KRW 10종목 실수치는 fetch_local.py.")


if __name__ == "__main__":
    main()
