"""작은 알트 데이터 수집 — 업비트 KRW 전 종목 분봉을 끌어와 저장(펌프 전략 검증용).

메이저 10개 말고 '확 뛰는 작은 알트'를 테스트하려면 그 코인들 데이터가 필요하다.
업비트 KRW 마켓 전 종목(약 200개)의 15분봉을 받아 alt_data/ 에 저장.
이후 alt_momentum_test.py 가 '급등 잡아 짧게 타기'를 이 진짜 알트들로 검증한다.

한국망 PC에서 실행(업비트=한국IP). 시간 좀 걸림(전 종목이라). pyupbit 필요.

사용법:
    python scan_pumpers.py            # 전 종목 15분봉 2000개(약 3주)
    python scan_pumpers.py 1500       # 봉 수 지정
이후:  git add alt_data && git commit -m "알트 데이터" && git push
"""
from __future__ import annotations
import os, sys, time
import pyupbit

from trader import backtest

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "alt_data")

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2000
    tickers = pyupbit.get_tickers(fiat="KRW")
    if not tickers:
        print("티커 목록 실패 — 한국망에서 실행했는지 확인"); return
    os.makedirs(OUT, exist_ok=True)
    print(f"업비트 KRW 전 종목 {len(tickers)}개 × 15분봉 {count}개 수집 시작...")
    print("(전 종목이라 5~15분 걸려요. 그냥 두세요)\n")
    saved = 0
    for i, tk in enumerate(tickers):
        try:
            df = backtest.fetch_minutes(tk, 15, count)
            if df is None or len(df) < 250:
                continue
            df.to_csv(os.path.join(OUT, f"{tk}.csv"))
            saved += 1
            if saved % 20 == 0:
                print(f"  ...{saved}종목 저장 ({i+1}/{len(tickers)} 확인)")
        except Exception:
            continue
        time.sleep(0.1)
    print(f"\n완료: {saved}종목 저장 → {os.path.relpath(OUT, BASE)}/")
    print("커밋:  git add alt_data && git commit -m \"알트 데이터\" && git push")


if __name__ == "__main__":
    main()
