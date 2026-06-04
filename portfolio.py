"""포트폴리오 분할매수 백테스트 실행 진입점.

사용법:
    python portfolio.py                          # 최신 2000봉
    python portfolio.py 3000                     # 종목당 봉 개수 지정
    python portfolio.py 3000 "2024-12-15"        # 종료일 지정(그 이전 과거구간=상승장 검증)

config.yaml의 `portfolio:` 섹션(총자본·종목리스트·분할/익절 파라미터)을 사용해,
다종목 분할매수 전략을 수수료 차감 기준으로 시뮬레이션합니다.
"""
from __future__ import annotations

import os
import sys

import yaml

from trader import portfolio

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    to_kst = sys.argv[2] if len(sys.argv) > 2 else None
    unit = int(cfg.get("timeframe_min", 1))
    tickers = cfg.get("portfolio", {}).get("tickers", [])

    window = f"~{to_kst} 이전" if to_kst else "최신"
    print(f"\n포트폴리오 {len(tickers)}종목 × {unit}분봉 {count}개 ({window}) 수집/시뮬레이션...")
    results = portfolio.run(cfg, unit, count, to_kst=to_kst)
    print("\n" + portfolio.summarize(results, cfg))


if __name__ == "__main__":
    main()
