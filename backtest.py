"""백테스트 실행 진입점.

사용법:
    python backtest.py                # config.yaml 설정 + 기본 3000봉
    python backtest.py 5000           # 봉 개수 지정
    python backtest.py 5000 KRW-ETH   # 봉 개수 + 티커 지정

과거 분봉을 받아 '국면판단 + 국면별 전략'을 수수료 차감 기준으로 시뮬레이션합니다.
"""
from __future__ import annotations

import os
import sys

import yaml

from trader import backtest

BASE = os.path.dirname(os.path.abspath(__file__))

# Windows 콘솔(cp949)에서도 한글/이모지가 깨지지 않도록 UTF-8 출력
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    if len(sys.argv) > 2:
        cfg["ticker"] = sys.argv[2]
    unit = int(cfg.get("timeframe_min", 1))

    print(f"\n과거 {cfg['ticker']} {unit}분봉 {count}개 수집 중...")
    df = backtest.fetch_minutes(cfg["ticker"], unit, count)
    if df is None or len(df) < 250:
        print("데이터 수집 실패 또는 부족합니다 (네트워크/티커 확인).")
        return
    print(f"수집 완료: {len(df)}봉  ({df.index[0]} ~ {df.index[-1]})")

    res = backtest.run(df, cfg)
    print("\n" + backtest.summarize(res, cfg))


if __name__ == "__main__":
    main()
