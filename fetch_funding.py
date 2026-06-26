"""바이낸스 무기한선물 펀딩비 히스토리 수집 — 포지셔닝(무상관) 엣지 검증용.

펀딩비 = 무기한선물 롱/숏이 서로 주고받는 수수료(8시간마다 정산).
  + (양수): 롱이 숏에게 지불 = 롱 과열/과레버리지 → 조정 위험
  - (음수): 숏이 롱에게 지불 = 숏 과밀 → 숏 스퀴즈로 튈 여지(롱 신호)
가격이 아니라 '포지셔닝'이라 우리 가격기반 엔진들과 무상관. 3년 히스토리 받아짐.

⚠️ 실행: 바이낸스 fapi.binance.com — 한국망에서 접근됨(클라우드는 차단). 집/한국PC에서.
  API 키 불필요(공개 데이터).

저장: funding_data/<KRW-코인>.csv  (열: ts_utc, funding_rate)
  ts_utc = 정산 시각(UTC), funding_rate = 그 회차 펀딩비(소수, 예 0.0001 = 0.01%)

사용법:
    python fetch_funding.py              # 10종목, 약 3.2년(2023-01~)
    python fetch_funding.py 2023-06-01   # 시작일 지정
이후:
    git add funding_data && git commit -m "펀딩비 데이터" && git push
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "funding_data")
URL = "https://fapi.binance.com/fapi/v1/fundingRate"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SYMBOL = {
    "KRW-BTC": "BTCUSDT", "KRW-ETH": "ETHUSDT", "KRW-XRP": "XRPUSDT",
    "KRW-SOL": "SOLUSDT", "KRW-ADA": "ADAUSDT", "KRW-DOGE": "DOGEUSDT",
    "KRW-TRX": "TRXUSDT", "KRW-LINK": "LINKUSDT", "KRW-AVAX": "AVAXUSDT",
    "KRW-DOT": "DOTUSDT",
}


def fetch_funding(symbol: str, start_ms: int) -> pd.DataFrame:
    """start_ms부터 현재까지 펀딩비 전체(8시간 간격). 1000개씩 forward 페이지네이션."""
    rows = []
    cur = start_ms
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    while cur < now_ms:
        params = {"symbol": symbol, "startTime": cur, "limit": 1000}
        data = None
        for attempt in range(4):
            try:
                r = requests.get(URL, params=params, timeout=12)
                if r.status_code == 200:
                    data = r.json(); break
            except Exception:
                pass
            time.sleep(0.5 * (attempt + 1))
        if not data:
            break
        rows += data
        last = data[-1]["fundingTime"]
        if len(data) < 1000:
            break
        cur = last + 1
        time.sleep(0.25)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates("fundingTime")
    df["ts_utc"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = df["fundingRate"].astype(float)
    return df[["ts_utc", "funding_rate"]].set_index("ts_utc").sort_index()


def main():
    args = sys.argv[1:]
    start = args[0] if args else "2023-01-01"
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    os.makedirs(OUT, exist_ok=True)
    print(f"수집: {len(SYMBOL)}종목 펀딩비 ({start}~현재, 8시간 간격)\n")
    for tk, sym in SYMBOL.items():
        df = fetch_funding(sym, start_ms)
        if df.empty:
            print(f"  [{tk}] 수집 실패 — 차단/심볼 확인"); continue
        path = os.path.join(OUT, f"{tk}.csv")
        df.to_csv(path)
        avg = df["funding_rate"].mean() * 100
        print(f"  [{tk}] {len(df):5}회  ({df.index[0].date()}~{df.index[-1].date()})  "
              f"평균 {avg:+.4f}%/8h → {path.split(os.sep)[-1]}")
    print("\n커밋:  git add funding_data && git commit -m \"펀딩비 데이터\" && git push")
    print("이후:  (푸시하면) 여기서 funding_backtest.py 로 엣지 검증")


if __name__ == "__main__":
    main()
