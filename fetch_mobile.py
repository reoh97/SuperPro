"""폰/최소환경용 분봉 수집기 — 외부 라이브러리 0개(파이썬 표준 라이브러리만).

업비트 공개 캔들 API를 직접 호출해 10종목 × 국면별 분봉을 backtest_data/ 에 CSV로 저장한다.
pandas/pyupbit 설치가 어려운 환경(안드로이드 Termux, iOS a-Shell, Pydroid 등)을 위한 것.
ⓘ 업비트는 '한국 IP'에서만 응답함 → 한국 통신망에 연결된 폰이면 OK(클라우드는 차단).
ⓘ API 키 불필요(시세 조회는 공개).

저장형식: backtest_data/<국면>__<티커>.csv  (헤더: dt,open,high,low,close,volume)
  → circuit_backtest.py / portfolio 백테스트가 그대로 읽는다.

사용법:
    python fetch_mobile.py            # bull/sideways/bear 전체
    python fetch_mobile.py bear       # 한 국면만
이후:
    git add backtest_data && git commit -m "분봉 수집" && git push
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "backtest_data")
UNIT = 15
TICKERS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",
           "KRW-DOGE", "KRW-TRX", "KRW-LINK", "KRW-AVAX", "KRW-DOT"]
# 국면: (봉수, 종료시각 KST 또는 None=최신) — circuit_backtest.REGIMES 와 동일
REGIMES = {
    "bull":     (3000, "2024-12-15 00:00:00"),
    "sideways": (3000, "2024-10-01 00:00:00"),
    "bear":     (6000, None),
}
API = "https://api.upbit.com/v1/candles/minutes/{unit}"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _kst_to_utc_param(kst: str) -> str:
    """'YYYY-MM-DD HH:MM:SS'(KST) → 업비트 to 파라미터용 UTC ISO 문자열."""
    dt = datetime.strptime(kst, "%Y-%m-%d %H:%M:%S") - timedelta(hours=9)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(market: str, count: int, to: str | None):
    params = {"market": market, "count": str(count)}
    if to:
        params["to"] = to
    url = API.format(unit=UNIT) + "?" + urllib.parse.urlencode(params)
    # 1) 파이썬 네트워크(urllib) 시도 — PC 등 대부분 환경
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(0.4 * (attempt + 1))
    # 2) 폴백: curl (a-Shell 등 파이썬 네트워크가 막힌 환경. curl은 됨)
    tmp = os.path.join(BASE, "_upbit_tmp.json")
    for attempt in range(3):
        rc = os.system(f'curl -s -H "Accept: application/json" "{url}" -o "{tmp}"')
        try:
            with open(tmp, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            if isinstance(data, list):
                return data
        except Exception:
            pass
        time.sleep(0.5 * (attempt + 1))
    return []


def fetch(market: str, total: int, to_kst: str | None):
    """total개 분봉을 페이지네이션으로 수집. 반환: 시간 오름차순 행 리스트."""
    to = _kst_to_utc_param(to_kst) if to_kst else None
    rows = {}
    remaining = total
    while remaining > 0:
        n = min(200, remaining)
        page = _get(market, n, to)
        if not page:
            break
        for c in page:
            rows[c["candle_date_time_kst"]] = (
                c["opening_price"], c["high_price"], c["low_price"],
                c["trade_price"], c["candle_acc_trade_volume"])
        # 가장 오래된 캔들 시각(UTC)을 다음 to 로 → 더 과거 페이지
        oldest = page[-1]["candle_date_time_utc"]
        to = oldest if oldest.endswith("Z") else oldest + "Z"
        remaining -= len(page)
        if len(page) < n:
            break
        time.sleep(0.2)   # 레이트리밋 보호
    return sorted(rows.items())  # [(dt, (o,h,l,c,v)), ...] 오름차순


def main():
    args = sys.argv[1:]
    keys = [args[0]] if args and args[0] in REGIMES else list(REGIMES)
    os.makedirs(OUT, exist_ok=True)
    saved = 0
    for k in keys:
        total, to_kst = REGIMES[k]
        print(f"\n[{k}] count={total} to={to_kst or '최신'}")
        for tk in TICKERS:
            try:
                data = fetch(tk, total, to_kst)
            except Exception as e:
                print(f"  {tk}: 실패({e})"); continue
            if len(data) < 250:
                print(f"  {tk}: 부족({len(data)}봉) 건너뜀"); continue
            path = os.path.join(OUT, f"{k}__{tk}.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["dt", "open", "high", "low", "close", "volume"])
                for dt, (o, h, l, c, v) in data:
                    w.writerow([dt.replace("T", " "), o, h, l, c, v])
            saved += 1
            print(f"  {tk}: {len(data)}봉 저장")
    tmp = os.path.join(BASE, "_upbit_tmp.json")
    if os.path.exists(tmp):
        os.remove(tmp)
    print(f"\n완료: {saved}개 파일 → backtest_data/")
    print("다음:  git add backtest_data && git commit -m \"분봉 수집\" && git push")


if __name__ == "__main__":
    main()
