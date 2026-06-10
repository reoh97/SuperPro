"""일봉 수집기 — 중장기 추세추종 검증용. 업비트 일봉을 길게(기본 3년) 받아 저장.

중장기(몇 주~몇 달 홀딩) 전략은 일봉 다년치로 검증해야 한다(15분봉 1개월론 불가).
한국망 PC에서 실행 → daily_data/<티커>.csv 저장 → push → position_backtest가 사용.

사용법:
    python fetch_daily.py                # 10종목 × 약 3년(1100일)
    python fetch_daily.py 1500           # 일수 지정
이후:  git add daily_data && git commit -m "일봉" && git push
"""
from __future__ import annotations
import os, sys, time
import pandas as pd
import pyupbit
import yaml

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "daily_data")

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def fetch_days(ticker: str, count: int) -> pd.DataFrame:
    """일봉 count개(최신부터 과거로 페이지네이션, 200개씩)."""
    frames = []; to = None; remaining = count
    while remaining > 0:
        n = min(200, remaining)
        df = None
        for _ in range(4):
            df = pyupbit.get_ohlcv(ticker, interval="day", count=n, to=to)
            if df is not None and len(df) > 0:
                break
            time.sleep(0.5)
        if df is None or len(df) == 0:
            break
        frames.append(df)
        to = df.index[0].strftime("%Y-%m-%d %H:%M:%S")   # 다음 페이지=더 과거
        remaining -= len(df)
        if len(df) < n:
            break
        time.sleep(0.2)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="first")]


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    tickers = cfg["portfolio"]["tickers"]
    count = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1100
    os.makedirs(OUT, exist_ok=True)
    print(f"일봉 수집: {len(tickers)}종목 × {count}일\n")
    for tk in tickers:
        df = fetch_days(tk, count)
        if df is None or len(df) < 100:
            print(f"  [{tk}] 실패/부족 — 건너뜀"); continue
        path = os.path.join(OUT, f"{tk}.csv")
        df.to_csv(path)
        print(f"  [{tk}] {len(df)}일 ({df.index[0].date()} ~ {df.index[-1].date()}) → {os.path.relpath(path, BASE)}")
    print("\n커밋:  git add daily_data && git commit -m \"일봉\" && git push")


if __name__ == "__main__":
    main()
