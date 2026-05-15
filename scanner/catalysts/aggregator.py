"""카탈리스트 통합기 — 개별 소스 호출 + 최고 점수 hit 선택."""

from __future__ import annotations

import logging
from datetime import date

from scanner.catalysts.types import (
    KIND_SCORE,
    CatalystHit,
    CatalystKind,
    CatalystScore,
)

logger = logging.getLogger(__name__)


def aggregate_catalyst(symbol: str, target_date: date) -> CatalystScore:
    """모든 소스 호출 → 가장 높은 점수 hit 선택. 소스별 예외는 격리."""
    from scanner.catalysts import (  # 지연 import
        finnhub_news,
        nasdaq_earnings,
        prnewswire,
        recommendations,
    )

    hits: list[CatalystHit] = []
    sources = [
        ("nasdaq_earnings", nasdaq_earnings.fetch),
        ("recommendations", recommendations.fetch),
        ("prnewswire", prnewswire.fetch),
        ("finnhub_news", finnhub_news.fetch),
    ]
    for name, fn in sources:
        try:
            hit = fn(symbol, target_date)
            if hit is not None:
                hits.append(hit)
        except Exception as exc:
            logger.warning("Catalyst source %s failed for %s: %s", name, symbol, exc)

    if not hits:
        return CatalystScore(
            score=0,
            primary_kind=CatalystKind.NONE,
            summary="(no catalyst)",
            source="",
            all_hits=[],
        )

    primary = max(hits, key=lambda h: KIND_SCORE[h.kind])
    return CatalystScore(
        score=KIND_SCORE[primary.kind],
        primary_kind=primary.kind,
        summary=primary.headline[:200],
        source=primary.source,
        all_hits=hits,
    )
