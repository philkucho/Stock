"""Finnhub 일반 뉴스 — free tier 60 calls/min. 헤드라인 정규식 분류.

API key는 FINNHUB_API_KEY 환경변수. 없으면 dummy hit 안 반환 (None).
헤드라인 정확도가 60-70% 수준이라 Score 1점 (`NEWS`)으로 가중 낮게 부여.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, time, timedelta
from functools import lru_cache

import httpx

from scanner.catalysts.types import CatalystHit, CatalystKind

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"

UPGRADE_RE = re.compile(r"\b(upgrade|raised|raises|outperform|buy rating|price target rais)\b", re.I)
FDA_RE = re.compile(r"\b(FDA approv|FDA clear|breakthrough designation|phase\s*[ii]+|trial)\b", re.I)
MA_RE = re.compile(r"\b(acquire|acquisition|merger|to buy|all-cash|takeover|tender offer)\b", re.I)


def _api_key() -> str | None:
    return os.environ.get("FINNHUB_API_KEY")


@lru_cache(maxsize=256)
def _company_news(symbol: str, from_iso: str, to_iso: str) -> list[dict]:
    key = _api_key()
    if not key:
        return []
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(
                f"{FINNHUB_BASE}/company-news",
                params={"symbol": symbol, "from": from_iso, "to": to_iso, "token": key},
            )
            r.raise_for_status()
            return r.json() or []
    except Exception as exc:
        logger.debug("Finnhub company-news failed for %s: %s", symbol, exc)
        return []


def _classify(headline: str) -> CatalystKind:
    if UPGRADE_RE.search(headline):
        return CatalystKind.UPGRADE
    if FDA_RE.search(headline) or MA_RE.search(headline):
        return CatalystKind.FDA_MA
    return CatalystKind.NEWS


def fetch(symbol: str, target_date: date) -> CatalystHit | None:
    """target_date 직전 24시간 헤드라인 중 가장 강한 카탈리스트 1개."""
    from_d = (target_date - timedelta(days=1)).isoformat()
    to_d = target_date.isoformat()
    news = _company_news(symbol, from_d, to_d)
    if not news:
        return None

    # 가장 강한 분류 선택
    best: tuple[CatalystKind, dict] | None = None
    rank = {CatalystKind.FDA_MA: 3, CatalystKind.UPGRADE: 2, CatalystKind.NEWS: 1}
    for item in news:
        headline = item.get("headline") or item.get("summary") or ""
        if not headline:
            continue
        kind = _classify(headline)
        if best is None or rank.get(kind, 0) > rank.get(best[0], 0):
            best = (kind, item)

    if best is None:
        return None
    kind, item = best
    return CatalystHit(
        kind=kind,
        headline=item.get("headline", ""),
        source="finnhub",
        url=item.get("url"),
    )
