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
from trader.live import LiveTrader
from trader.news import NewsFeed
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


def build_engine(cfg: dict) -> LiveTrader:
    mode = cfg.get("mode", "paper")
    # 모드별 상태 파일 분리 (모의/실거래 혼동 방지)
    state_path = os.path.join(BASE, "data", f"live_{mode}.json")

    ai_cfg = cfg.get("ai", {})
    advisor = AIAdvisor(cfg)
    news = NewsFeed(
        feeds=cfg.get("news_feeds", []),
        cache_sec=int(ai_cfg.get("news_cache_sec", 900)),
        max_items=int(ai_cfg.get("news_max_items", 12)),
    )
    # 실거래: 단일계정 장부기반 브로커(자기 서브예산/수량만 사용 → 코어 엔진과 한 계정 공유 안전)
    broker = None
    if mode == "live":
        from trader.account import LedgerBroker
        up = cfg.get("upbit", {})
        broker = LedgerBroker(up.get("access_key", ""), up.get("secret_key", ""),
                              fee=float(cfg["trade"]["fee"]))
    return LiveTrader(cfg, advisor=advisor, news=news, state_path=state_path, broker=broker)


def main():
    import sys
    load_dotenv(os.path.join(BASE, ".env"))   # ANTHROPIC_API_KEY 등 로드
    # 설정 파일 선택: 인자로 주면 그걸, 없으면 config.yaml(=프로토타입1)
    #   예) python run.py config_proto2.yaml   → 프로토타입2(공격형)
    cfg_name = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg_path = cfg_name if os.path.isabs(cfg_name) else os.path.join(BASE, cfg_name)
    cfg = load_config(cfg_path)
    print(f"[설정] {os.path.basename(cfg_path)}")
    mode = cfg.get("mode", "paper")
    engine = build_engine(cfg)
    engine.start_loop()   # 시세/국면 평가 루프 기동 (매매는 대시보드에서 활성화)

    app = create_app(engine)
    print(f"\n[ 업비트 자동매매 - 멀티코인 국면별 ({('실거래' if mode=='live' else '모의')} 모드) ]")
    print(f"종목:      {len(engine.tickers)}개  (각 {engine.per_coin:,.0f}원 / {engine.n_tranche}분할)")
    if engine.advisor and engine.advisor.enabled:
        print(f"AI 장세:   사용 ({engine.advisor.model}) — BEAR/risk_off시 신규매수 중단")
    else:
        why = engine.advisor.disabled_reason if engine.advisor else "비활성"
        print(f"AI 장세:   미사용 - {why}")
    if mode == "live":
        print("⚠️ 실거래 모드: 장부기반 단일계정 브로커로 실주문 실행. "
              "코어(run_longterm.py)와 한 계정 공유 가능 — reconcile.py로 정합성 점검 권장.")
    print("대시보드:  http://127.0.0.1:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
