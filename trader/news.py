"""영문 코인 뉴스 수집 (RSS).

CoinDesk·Cointelegraph 등의 RSS 피드에서 최신 헤드라인을 모아
AI 판단에 넘길 수 있도록 간결한 형태로 반환합니다.
네트워크 비용/토큰 절약을 위해 일정 시간 캐시합니다.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import List

import feedparser


@dataclass
class Headline:
    title: str
    summary: str
    source: str
    published: str


class NewsFeed:
    def __init__(self, feeds: List[str], cache_sec: int = 900, max_items: int = 12):
        self.feeds = feeds
        self.cache_sec = cache_sec
        self.max_items = max_items
        self._lock = threading.Lock()
        self._cache: List[Headline] = []
        self._fetched_at = 0.0

    def latest(self, force: bool = False) -> List[Headline]:
        """최신 헤드라인 목록. 캐시가 유효하면 캐시를 반환."""
        with self._lock:
            fresh = (time.time() - self._fetched_at) < self.cache_sec
            if self._cache and fresh and not force:
                return list(self._cache)

        items = self._fetch()
        with self._lock:
            if items:
                self._cache = items
                self._fetched_at = time.time()
            return list(self._cache)

    def _fetch(self) -> List[Headline]:
        out: List[Headline] = []
        for url in self.feeds:
            try:
                parsed = feedparser.parse(url)
                source = parsed.feed.get("title", url)
                for entry in parsed.entries[: self.max_items]:
                    summary = entry.get("summary", "") or ""
                    # 요약은 토큰 절약을 위해 길이 제한 + 태그 제거(간단)
                    summary = _strip_html(summary)[:300]
                    out.append(Headline(
                        title=entry.get("title", "").strip(),
                        summary=summary.strip(),
                        source=source,
                        published=entry.get("published", "") or entry.get("updated", ""),
                    ))
            except Exception:
                continue
        # 피드별로 섞여 들어오므로 최신 published 기준 정렬은 형식이 제각각이라 생략.
        # 각 피드 상위 항목들을 합쳐 max_items 개로 제한.
        return out[: self.max_items]


def _strip_html(text: str) -> str:
    """아주 단순한 HTML 태그 제거 (요약 정리용)."""
    import re
    return re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ")
