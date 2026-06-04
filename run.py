"""진입점: 설정을 읽어 엔진을 구성하고 웹 대시보드를 띄운다.

사용법:
    python run.py                # config.yaml 사용
    실행 후 브라우저에서 http://127.0.0.1:8000 접속

매매는 안전을 위해 시작 시 '정지' 상태입니다.
대시보드의 [매매 시작] 버튼을 눌러야 실제 평가/주문 루프가 동작합니다.
"""
from __future__ import annotations

import os

import uvicorn
import yaml

from trader.ai_advisor import AIAdvisor
from trader.broker import LiveBroker, PaperBroker
from trader.engine import Engine
from trader.news import NewsFeed
from trader.state import StateStore
from web.app import create_app

BASE = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path: str) -> None:
    """.env 파일이 있으면 KEY=VALUE 를 환경변수로 로드 (의존성 없이 간단 처리)."""
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


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_engine(cfg: dict) -> Engine:
    mode = cfg.get("mode", "paper")
    fee = float(cfg["trade"]["fee"])

    # 모드별로 상태 파일 분리 (모의/실거래 잔고 혼동 방지)
    state_path = os.path.join(BASE, "data", f"state_{mode}.json")
    store = StateStore(state_path)
    store.load(default_krw=float(cfg["paper"]["initial_krw"]))

    if mode == "live":
        broker = LiveBroker(
            access_key=cfg["upbit"].get("access_key", ""),
            secret_key=cfg["upbit"].get("secret_key", ""),
            ticker=cfg["ticker"], store=store, fee=fee,
        )
    else:
        broker = PaperBroker(store=store, fee=fee)

    # AI 뉴스 판단 구성
    ai_cfg = cfg.get("ai", {})
    advisor = AIAdvisor(cfg)
    news = NewsFeed(
        feeds=cfg.get("news_feeds", []),
        cache_sec=int(ai_cfg.get("news_cache_sec", 900)),
        max_items=int(ai_cfg.get("news_max_items", 12)),
    )
    return Engine(cfg, store, broker, advisor=advisor, news=news)


def main():
    load_dotenv(os.path.join(BASE, ".env"))   # ANTHROPIC_API_KEY 등 로드
    cfg = load_config(os.path.join(BASE, "config.yaml"))
    engine = build_engine(cfg)
    engine.start_loop()   # 시세 평가 루프 기동 (매매는 대시보드에서 활성화)

    app = create_app(engine)
    mode = cfg.get("mode", "paper")
    print(f"\n[ 업비트 자동매매 - {('실거래' if mode=='live' else '모의')} 모드 ]")
    if engine.advisor and engine.advisor.enabled:
        print(f"AI 판단:   사용 ({engine.advisor.model})")
    else:
        why = engine.advisor.disabled_reason if engine.advisor else "비활성"
        print(f"AI 판단:   미사용 - {why}")
    print("대시보드:  http://127.0.0.1:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
