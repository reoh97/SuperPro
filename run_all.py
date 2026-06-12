"""통합 실행기 — 새틀라이트(단기)+코어(중장기) 두 엔진을 한 프로세스에서 병행 구동하고
좌/우 2단 통합 대시보드를 띄운다.

  좌측: 🛰 새틀라이트(LiveTrader)  ·  우측: 🪨 코어(LongTrendTrader)
  둘 다 같은 업비트 계정 공유(장부 분리). 각 패널에서 개별 시작/정지.

사용법:
    python run_all.py            # config.yaml 사용
  브라우저: http://127.0.0.1:8000   (매매는 각 패널 [시작] 버튼으로 활성화 — 안전 기본 정지)
  ⚠️ mode: live 면 실주문이 나간다. 충분히 모의 검증 후 소액으로. 출금권한 금지.
"""
from __future__ import annotations

import os
import sys

import uvicorn
import yaml

from trader.ai_advisor import AIAdvisor
from trader.live import LiveTrader
from trader.longtrend import LongTrendTrader
from trader.news import NewsFeed
from web.app import create_combined_app

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def main():
    load_dotenv(os.path.join(BASE, ".env"))
    cfg_name = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg_path = cfg_name if os.path.isabs(cfg_name) else os.path.join(BASE, cfg_name)
    cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    mode = cfg.get("mode", "paper")

    # 실거래: 한 계정 공유 → 엔진별 장부기반 브로커(자기 서브예산/수량만). 모의면 None.
    sat_broker = core_broker = None
    if mode == "live":
        from trader.account import LedgerBroker
        up = cfg.get("upbit", {})
        fee = float(cfg.get("trade", {}).get("fee", 0.0005))
        sat_broker = LedgerBroker(up.get("access_key", ""), up.get("secret_key", ""), fee=fee)
        core_broker = LedgerBroker(up.get("access_key", ""), up.get("secret_key", ""), fee=fee)

    # 새틀라이트(단기 전술 + AI 게이트)
    ai_cfg = cfg.get("ai", {})
    advisor = AIAdvisor(cfg)
    news = NewsFeed(feeds=cfg.get("news_feeds", []),
                    cache_sec=int(ai_cfg.get("news_cache_sec", 900)),
                    max_items=int(ai_cfg.get("news_max_items", 12)))
    satellite = LiveTrader(cfg, advisor=advisor, news=news,
                           state_path=os.path.join(BASE, "data", f"live_{mode}.json"),
                           broker=sat_broker)

    # 코어(중장기 추세추종)
    core_enabled = cfg.get("longterm", {}).get("enabled", False)
    core = LongTrendTrader(cfg, state_path=os.path.join(BASE, "data", f"longterm_{mode}.json"),
                           broker=core_broker)

    satellite.start_loop()
    core.start_loop()       # 둘 다 평가 루프 기동(매매는 대시보드에서 활성화)

    app = create_combined_app(satellite, core)
    print("=" * 64)
    print(f"  SuperPro 통합 대시보드  ({'실거래' if mode=='live' else '모의'} 모드)")
    print(f"  🛰 새틀 {satellite.per_coin:,.0f}원/종목 ×{len(satellite.tickers)}  "
          f"|  🪨 코어 {core.per_coin:,.0f}원/종목 ×{len(core.tickers)}"
          f"{'' if core_enabled else '  (longterm.enabled=false — 코어 매매 비활성)'}")
    if mode == "live":
        print("  ⚠️ 실거래: 같은 계정 공유. reconcile.py 로 정합성 점검 권장. 출금권한 금지.")
    print("  http://127.0.0.1:8000   (매매는 각 패널 [시작] 버튼)")
    print("=" * 64)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
