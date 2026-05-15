"""Daily Picks 엔드포인트 — 단타 스캐너 Stage 1/2 결과 조회·트리거."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import DailyPick, UniverseMember

router = APIRouter()


class PickOut(BaseModel):
    id: int
    pick_date: date
    rank: int
    symbol: str
    is_backup: bool
    total_score: Decimal
    gate_results: dict[str, Any]
    score_breakdown: dict[str, Any]
    pivot_price: Decimal
    stop_price: Decimal
    target_1r: Decimal
    target_2r: Decimal
    risk_per_share: Decimal
    position_size: int
    strategy_tag: str
    catalyst_summary: str | None
    catalyst_source: str | None
    market_context: dict[str, Any]
    sector: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UniverseMemberOut(BaseModel):
    id: int
    symbol: str
    source: str
    category: str | None
    base_score: Decimal
    valid_until: date | None
    enabled: bool
    extra: dict[str, Any]
    notes: str | None
    added_at: datetime
    last_revalidated_at: datetime | None

    model_config = {"from_attributes": True}


# ─────────── 조회 ───────────


@router.get("/today", response_model=list[PickOut])
async def picks_today(
    session: AsyncSession = Depends(get_session),
) -> list[DailyPick]:
    return await _picks_for(session, date.today())


@router.get("/{pick_date}", response_model=list[PickOut])
async def picks_for_date(
    pick_date: date,
    session: AsyncSession = Depends(get_session),
) -> list[DailyPick]:
    return await _picks_for(session, pick_date)


async def _picks_for(session: AsyncSession, pick_date: date) -> list[DailyPick]:
    stmt = (
        select(DailyPick)
        .where(DailyPick.pick_date == pick_date)
        .order_by(DailyPick.rank)
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("/universe/members", response_model=list[UniverseMemberOut])
async def list_universe(
    enabled_only: bool = Query(default=True),
    source: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[UniverseMember]:
    stmt = select(UniverseMember)
    if enabled_only:
        stmt = stmt.where(UniverseMember.enabled == True)  # noqa: E712
    if source:
        stmt = stmt.where(UniverseMember.source == source)
    stmt = stmt.order_by(UniverseMember.symbol)
    return list((await session.execute(stmt)).scalars().all())


# ─────────── 수동 트리거 (admin) ───────────


@router.post("/universe/refresh")
async def trigger_universe_refresh(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stage 1 universe 재구성 — 월 1회 cron이 호출하지만 수동 트리거도 허용."""
    from scanner.stage1_universe import refresh

    try:
        result = await refresh(session)
        return {"status": "ok", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/refresh")
async def trigger_picks_refresh(
    target_date: date | None = Query(default=None),
    equity: float = Query(default=25_000.0),
    after_hours_lenient: bool | None = Query(
        default=None,
        description="None=시간대 자동 감지 (장 마감 후/주말은 자동 lenient), True/False=강제",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stage 2 daily picks 재실행 — 매일 08:55 ET cron 외 수동 트리거."""
    from scanner.stage2_daily_picks import _serialize_picks, run_daily_picks

    try:
        target = target_date or date.today()
        picks = await run_daily_picks(
            session,
            target,
            account_equity=equity,
            after_hours_lenient=after_hours_lenient,
        )
        return {
            "status": "ok",
            "pick_date": target.isoformat(),
            "count": len(picks),
            "picks": _serialize_picks(picks),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
