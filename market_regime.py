"""AI 시장국면 판단 — 단독 실행 도구.

비트코인(시장 대표) 차트 요약 + 최신 영문 코인 뉴스를 Claude에게 주고,
현재 시장 국면(BULL/SIDEWAYS/BEAR) + risk_off + 이유를 받아 출력한다.

이것이 "뉴스 + 차트로 AI가 장을 결정"의 핵심 두뇌다. (과거 뉴스가 없어
백테스트로는 검증 불가 → 라이브 판단 레이어. ANTHROPIC_API_KEY 필요)

사용법:
    python market_regime.py            # config.yaml 기준 BTC로 판단
    python market_regime.py KRW-ETH    # 다른 코인을 시장대표로
"""
from __future__ import annotations

import os
import sys

import yaml

from trader import backtest
from trader.ai_advisor import AIAdvisor
from trader.market import build_market_summary
from trader.news import NewsFeed

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_dotenv(path: str) -> None:
    """.env 가 있으면 KEY=VALUE 를 환경변수로 로드(의존성 없이)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def main():
    load_dotenv(os.path.join(BASE, ".env"))
    with open(os.path.join(BASE, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    ticker = sys.argv[1] if len(sys.argv) > 1 else cfg.get("ticker", "KRW-BTC")
    unit = int(cfg.get("timeframe_min", 15))

    print(f"\n[{ticker}] 차트 수집 + 뉴스 수집 중...")
    df = backtest.fetch_minutes(ticker, unit, 250)
    if df is None or len(df) < 60:
        print("차트 수집 실패.")
        return
    summary = build_market_summary(df, cfg)

    ai_cfg = cfg.get("ai", {})
    news = NewsFeed(feeds=cfg.get("news_feeds", []),
                    cache_sec=int(ai_cfg.get("news_cache_sec", 900)),
                    max_items=int(ai_cfg.get("news_max_items", 12)))
    headlines = news.latest()

    print("\n=== 시장 차트 요약 (AI 입력) ===")
    print(summary)
    print(f"\n=== 수집 뉴스 {len(headlines)}건 ===")
    for h in headlines[:6]:
        print(f"  - [{h.source}] {h.title}")

    advisor = AIAdvisor(cfg)
    if not advisor.enabled:
        print(f"\n⚠️ AI 미사용: {advisor.disabled_reason}")
        print("   (ANTHROPIC_API_KEY 설정 후 다시 실행하면 AI 장세판단이 나옵니다)")
        return

    print(f"\nAI({advisor.model}) 판단 중...")
    view = advisor.decide_regime(summary, headlines)
    badge = {"BULL": "🟢 상승장(BULL)", "SIDEWAYS": "🟡 횡보장(SIDEWAYS)",
             "BEAR": "🔴 하락장(BEAR)", "ERROR": "⚠️ 오류"}.get(view.regime, view.regime)
    print("\n" + "=" * 60)
    print(f"  AI 시장국면 판단: {badge}")
    print(f"  확신도: {view.confidence}%    risk_off(긴급방어): {view.risk_off}")
    print(f"  이유: {view.reason}")
    print("=" * 60)


if __name__ == "__main__":
    main()
