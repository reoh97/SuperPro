"""바이낸스↔업비트 1분봉 동기화 수집 — 선행지표(lead-lag) 검증용.

바이낸스(선행)와 업비트(추종)의 같은 시각 1분봉을 받아 시각 정렬해 저장한다.
이후 leadlag_analyze.py 가 '바이낸스가 움직인 뒤 업비트가 수수료 넘게 따라오나'를 측정.

⚠️ 실행 환경:
  - 업비트: 한국 IP에서만 응답(클라우드 차단). → 집/한국망 PC에서 실행.
  - 바이낸스 api.binance.com: 한국에서 막히면 ↓ '대체' 참고(data.binance.vision 정적파일).
  - API 키 불필요(둘 다 공개 시세).

저장: leadlag_data/<코인>.csv  (열: ts_utc, upbit, binance, up_ret, bn_ret)
  ts_utc = 분 시작(UTC 기준, 두 거래소 동일 시각으로 정렬)
  *_ret  = 직전 분 대비 수익률(%) — 가격 단위(KRW vs USDT)가 달라 '수익률'로 비교.

사용법:
    python fetch_leadlag.py                 # 10종목, 최근 약 7일(10000분)
    python fetch_leadlag.py 3000            # 분 수 지정
    python fetch_leadlag.py 3000 KRW-BTC    # 특정 코인만
이후:
    git add leadlag_data && git commit -m "lead-lag 데이터" && git push
"""
from __future__ import annotations

import os
import sys
import time

import pandas as pd
import requests

from trader import backtest  # 업비트 분봉(페이지네이션 처리) 재사용

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "leadlag_data")
BINANCE_URL = "https://api.binance.com/api/v3/klines"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 업비트(KRW) → 바이낸스(USDT) 심볼. 전 종목 USDT 페어 존재.
SYMBOL = {
    "KRW-BTC": "BTCUSDT", "KRW-ETH": "ETHUSDT", "KRW-XRP": "XRPUSDT",
    "KRW-SOL": "SOLUSDT", "KRW-ADA": "ADAUSDT", "KRW-DOGE": "DOGEUSDT",
    "KRW-TRX": "TRXUSDT", "KRW-LINK": "LINKUSDT", "KRW-AVAX": "AVAXUSDT",
    "KRW-DOT": "DOTUSDT",
}


def fetch_binance(symbol: str, minutes: int) -> pd.DataFrame:
    """바이낸스 1분봉 minutes개(최근). openTime=UTC ms. 1000개씩 역방향 페이지네이션."""
    rows = []
    end = None  # endTime(ms); None=최신
    remaining = minutes
    while remaining > 0:
        n = min(1000, remaining)
        params = {"symbol": symbol, "interval": "1m", "limit": n}
        if end is not None:
            params["endTime"] = end
        data = None
        for attempt in range(4):
            r = requests.get(BINANCE_URL, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json(); break
            time.sleep(0.5 * (attempt + 1))
        if not data:
            break
        rows = data + rows                              # 앞쪽(더 과거)에 누적
        end = data[0][0] - 1                            # 다음 페이지는 더 과거
        remaining -= len(data)
        if len(data) < n:
            break
        time.sleep(0.25)                                # 레이트리밋 보호
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "openTime", "o", "h", "l", "c", "v", "closeTime",
        "qv", "trades", "tbv", "tqv", "ig"])
    df = df.drop_duplicates("openTime")
    df["ts_utc"] = pd.to_datetime(df["openTime"], unit="ms", utc=True)
    df["binance"] = df["c"].astype(float)
    return df[["ts_utc", "binance"]].set_index("ts_utc").sort_index()


def fetch_upbit(ticker: str, minutes: int) -> pd.DataFrame:
    """업비트 1분봉 minutes개(최근). KST 인덱스 → UTC 변환해 정렬."""
    df = backtest.fetch_minutes(ticker, 1, minutes)     # unit=1분
    if df is None or len(df) == 0:
        return pd.DataFrame()
    idx = pd.to_datetime(df.index)                      # KST(naive)
    ts_utc = (idx - pd.Timedelta(hours=9)).tz_localize("UTC")
    out = pd.DataFrame({"upbit": df["close"].astype(float).values}, index=ts_utc)
    out.index.name = "ts_utc"
    return out.sort_index()


def main():
    args = sys.argv[1:]
    minutes = int(args[0]) if args and args[0].isdigit() else 10000
    coins = [args[1]] if len(args) >= 2 else list(SYMBOL.keys())
    os.makedirs(OUT, exist_ok=True)
    print(f"수집: {len(coins)}종목 × {minutes}분  (바이낸스+업비트 1분봉)\n")

    for tk in coins:
        sym = SYMBOL.get(tk)
        if not sym:
            print(f"  [{tk}] 바이낸스 심볼 없음 — 건너뜀"); continue
        bn = fetch_binance(sym, minutes)
        up = fetch_upbit(tk, minutes)
        if bn.empty or up.empty:
            print(f"  [{tk}] 수집 실패(바이낸스 {len(bn)}, 업비트 {len(up)}) — 차단/제한 확인"); continue
        m = up.join(bn, how="inner")                    # 같은 UTC 분만 정렬 병합
        m["up_ret"] = m["upbit"].pct_change() * 100
        m["bn_ret"] = m["binance"].pct_change() * 100
        m = m.dropna()
        path = os.path.join(OUT, f"{tk}.csv")
        m.to_csv(path)
        print(f"  [{tk}] 정렬 {len(m)}분  ({m.index[0]} ~ {m.index[-1]} UTC) → {os.path.relpath(path, BASE)}")

    print("\n커밋:  git add leadlag_data && git commit -m \"lead-lag 데이터\" && git push")
    print("이후:  (푸시하면) 여기서 leadlag_analyze.py 로 지연 측정")
    print("\n※ 바이낸스가 한국망서 막히면: data.binance.vision 의 정적 CSV(월/일별 klines)를")
    print("   내려받아 같은 형식으로 맞추는 대체 경로를 안내해 드립니다.")


if __name__ == "__main__":
    main()
