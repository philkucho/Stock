"""PR Newswire RSS — FDA / M&A / 주요 IR 발표의 신뢰도 높은 1차 소스.

PR Newswire는 회사가 직접 송출하는 보도자료라서 헤드라인 정확도가 일반 뉴스보다 높음.
무료 RSS 피드를 feedparser로 파싱. 종목 심볼이 헤드라인/내용에 포함된 발표만 매칭.

피드 종류 (실전에서는 종목별·섹터별 피드 매핑):
- https://www.prnewswire.com/rss/financial-services-latest-news-list.rss
- https://www.prnewswire.com/rss/health-care-latest-news-list.rss
- https://www.prnewswire.com/rss/news-releases-list.rss

이 모듈은 plug-in 형태. PR_NEWSWIRE_FEEDS 환경변수에 콤마 구분 URL 입력 가능 (선택).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from functools import lru_cache

import feedparser

from scanner.catalysts.types import CatalystHit, CatalystKind

logger = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    "https://www.prnewswire.com/rss/news-releases-list.rss",
    "https://www.prnewswire.com/rss/health-care-latest-news-list.rss",
    "https://www.prnewswire.com/rss/financial-services-latest-news-list.rss",
]

FDA_RE = re.compile(r"\b(FDA approv|FDA clear|breakthrough|phase\s*[ii]+)\b", re.I)
MA_RE = re.compile(r"\b(acquire|acquisition|merger|all-cash|tender offer|to buy)\b", re.I)


def _feeds() -> list[str]:
    custom = os.environ.get("PR_NEWSWIRE_FEEDS")
    if custom:
        return [u.strip() for u in custom.split(",") if u.strip()]
    return DEFAULT_FEEDS


@lru_cache(maxsize=8)
def _fetch_feed(url: str) -> list[dict]:
    """RSS 피드 1회 fetch. 5분 캐시 효과 — lru_cache는 프로세스 라이프타임."""
    try:
        parsed = feedparser.parse(url)
        return parsed.entries
    except Exception as exc:
        logger.debug("feedparser failed for %s: %s", url, exc)
        return []


def fetch(symbol: str, target_date: date) -> CatalystHit | None:
    """target_date 직전 24시간 PR Newswire 발표 중 symbol 매칭 헤드라인."""
    cutoff = datetime.combine(target_date, datetime.min.time()) - timedelta(days=1)
    matched: list[tuple[CatalystKind, dict]] = []
    rank = {CatalystKind.FDA_MA: 3, CatalystKind.NEWS: 1}

    for url in _feeds():
        for entry in _fetch_feed(url):
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = f"{title} {summary}"
            # 심볼이 단어 경계로 등장해야 매칭 (CRM 같은 단어 조각 방지)
            if not re.search(rf"\b{re.escape(symbol)}\b", text):
                continue

            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                pub_dt = datetime(*published[:6])
                if pub_dt < cutoff:
                    continue

            if FDA_RE.search(text) or MA_RE.search(text):
                kind = CatalystKind.FDA_MA
            else:
                kind = CatalystKind.NEWS

            matched.append((kind, entry))

    if not matched:
        return None
    best = max(matched, key=lambda m: rank.get(m[0], 0))
    kind, entry = best
    return CatalystHit(
        kind=kind,
        headline=entry.get("title", ""),
        source="prnewswire",
        url=entry.get("link"),
    )
