"""연속 15분봉 수집기 — 정확한 새틀라이트(단기) 백테스트용. 한국 IP에서 실행.

기존 backtest_data/ 는 국면별 '구간 3개'뿐이라 새틀라이트 백테스트가 근사였다.
이 스크립트는 10종목 × 연속 N일치 15분봉을 받아 min15_data/<티커>.csv 로 저장 →
backtest_full.py 가 코어(일봉)와 같은 기간으로 정확히 합산 백테스트한다.

⚠️ 업비트는 한국 IP에서만 응답(클라우드 403). 집/한국망 PC·폰에서 실행할 것. API키 불필요.

사용법:
    python fetch_15m.py             # 10종목 × 약 3년(1100일)치 15분봉
    python fetch_15m.py 730         # 일수 지정(예: 2년)
이후:  git add min15_data && git commit -m "연속 15분봉" && git push
       → 클라우드 세션에서 python backtest_full.py 로 정확 백테스트
"""
from __future__ import annotations
import os, sys, time
import yaml

from trader import backtest

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "min15_data")
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    tickers = cfg["portfolio"]["tickers"]
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1100
    bars = days * 96            # 15분봉: 하루 96개
    os.makedirs(OUT, exist_ok=True)

    print(f"연속 15분봉 수집: {len(tickers)}종목 × {days}일(≈{bars:,}봉)")
    print("⚠️ 업비트=한국IP 필요. 종목당 수백 페이지라 몇 분 걸립니다.\n")
    saved = 0
    for tk in tickers:
        t0 = time.time()
        df = backtest.fetch_minutes(tk, 15, bars)      # 200개씩 페이지네이션(내장)
        if df is None or len(df) == 0:
            print(f"  {tk}: 실패(차단/레이트리밋?) — 한국 IP인지 확인"); continue
        path = os.path.join(OUT, f"{tk}.csv")
        df.to_csv(path)
        saved += 1
        print(f"  {tk}: {len(df):,}봉  {df.index[0].date()}~{df.index[-1].date()}  "
              f"({time.time()-t0:.0f}s)")
    print(f"\n완료: {saved}/{len(tickers)}종목 → {os.path.relpath(OUT, BASE)}/")
    if saved:
        print("다음: git add min15_data && git commit -m '연속 15분봉' && git push")
        print("      그 뒤 (클라우드/한국 무관)  python backtest_full.py")


if __name__ == "__main__":
    main()
