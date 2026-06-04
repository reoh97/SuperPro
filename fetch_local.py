"""로컬 분봉 수집기 — 거래소 접근 가능한 환경(예: 한국 IP)에서 실행.

업비트 분봉(config의 10종목)을 국면별 구간으로 받아 backtest_data/ 에 CSV로 저장합니다.
그 폴더를 커밋·푸시하면, 거래소가 막힌 환경(클라우드 세션 등)에서도
circuit_backtest.py 가 동일 데이터로 실수치를 뽑습니다.

사용법:
    python fetch_local.py                 # bull/sideways/bear 모두 수집
    python fetch_local.py bear            # 특정 국면만
    python fetch_local.py bear 6000       # 봉수 지정
    python fetch_local.py bull 3000 "2024-12-15 00:00:00"   # 봉수·종료시점 지정

저장 후:
    git add backtest_data && git commit -m "백테스트용 분봉 수집" && git push
"""
from __future__ import annotations

import os
import sys

import yaml

from circuit_backtest import DATA_DIR, REGIMES
from trader import backtest

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    unit = int(cfg.get("timeframe_min", 15))
    tickers = cfg["portfolio"]["tickers"]

    args = sys.argv[1:]
    if args and args[0] in REGIMES:
        keys = [args[0]]
        if len(args) >= 2:
            REGIMES[args[0]]["count"] = int(args[1])
        if len(args) >= 3:
            REGIMES[args[0]]["to_kst"] = args[2]
    else:
        keys = list(REGIMES.keys())

    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"수집 대상: {len(tickers)}종목 × {len(keys)}국면  ({unit}분봉)\n")

    saved = 0
    for k in keys:
        meta = REGIMES[k]
        print(f"[{meta['label']}] count={meta['count']} to_kst={meta['to_kst'] or '최신'}")
        for tk in tickers:
            df = backtest.fetch_minutes(tk, unit, meta["count"], to_kst=meta["to_kst"])
            if df is None or len(df) < 250:
                print(f"  [{tk}] 실패/부족 — 건너뜀")
                continue
            path = os.path.join(DATA_DIR, f"{k}__{tk}.csv")
            df.to_csv(path)
            saved += 1
            print(f"  [{tk}] {len(df)}봉 → {os.path.relpath(path, BASE)}")
        print()

    print(f"완료: {saved}개 파일 저장 → {os.path.relpath(DATA_DIR, BASE)}/")
    print("커밋:  git add backtest_data && git commit -m \"백테스트용 분봉 수집\" && git push")
    print("이후:  python circuit_backtest.py   (CSV를 자동으로 우선 사용)")


if __name__ == "__main__":
    main()
