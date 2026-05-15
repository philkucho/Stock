"""Intraday 추천 중복 방지.

advisor_recommendations 테이블에서 같은 symbol에 대해
N분 안에 이미 추천이 있으면 skip.

advisor_recommendations.UNIQUE(rec_date, symbol, rec_type)만으로는
하루에 한 번만 같은 type 추천 가능 — 너무 빡빡. 30분 간격으로 다시 추천 OK해야 함.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AdvisorRecommendation


async def has_recent_intraday(
    session: AsyncSession,
    symbol: str,
    *,
    window_minutes: int = 30,
) -> bool:
    """같은 종목에 대해 N분 안에 intraday_* 추천이 있는지.

    pending/approved/rejected/expired 모두 포함 — 의사결정 노이즈 차단.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    stmt = (
        select(AdvisorRecommendation.id)
        .where(AdvisorRecommendation.symbol == symbol.upper())
        .where(AdvisorRecommendation.rec_type.like("intraday%"))
        .where(AdvisorRecommendation.created_at >= cutoff)
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar() is not None
