"""애널리스트 업그레이드/다운그레이드 — Finnhub /stock/recommendation.

월별 buy/strongBuy/hold/sell/strongSell 카운트. 직전 달 대비 buy↑면 업그레이드 모멘텀.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from functools import lru_cache

import httpx

from scanner.catalysts.types import CatalystHit, CatalystKind

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"


def _api_key() -> str | None:
    return os.environ.get("FINNHUB_API_KEY")


@lru_cache(maxsize=256)
def _recommendations(symbol: str) -> list[dict]:
    key = _api_key()
    if not key:
        return []
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(
                f"{FINNHUB_BASE}/stock/recommendation",
                params={"symbol": symbol, "token": key},
            )
            r.raise_for_status()
            return r.json() or []
    except Exception as exc:
        logger.debug("Finnhub recommendation failed for %s: %s", symbol, exc)
        return []


def fetch(symbol: str, target_date: date) -> CatalystHit | None:
    recs = _recommendations(symbol)
    if not recs:
        return None
    # 가장 최근 2개월 비교
    recs_sorted = sorted(recs, key=lambda r: r.get("period", ""), reverse=True)
    if len(recs_sorted) < 2:
        return None
    latest, prior = recs_sorted[0], recs_sorted[1]
    latest_buy = (latest.get("buy", 0) or 0) + (latest.get("strongBuy", 0) or 0)
    prior_buy = (prior.get("buy", 0) or 0) + (prior.get("strongBuy", 0) or 0)
    if latest_buy > prior_buy:
        return CatalystHit(
            kind=CatalystKind.UPGRADE,
            headline=f"Analyst buy ratings ↑ ({prior_buy} → {latest_buy}) [{latest.get('period', '?')}]",
            source="finnhub.recommendation",
        )
    return None
