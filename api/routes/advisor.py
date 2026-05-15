"""AI 자문 에이전트 라우트.

POST /api/advisor/morning-brief                     — 장 시작 전 자문 트리거 (멱등)
GET  /api/advisor/recommendations/today             — 오늘 추천 조회
GET  /api/advisor/recommendations/{id}              — 단일 조회 (reasoning 포함)
POST /api/advisor/recommendations/{id}/approve      — 사용자 승인 → trade_plan upsert
POST /api/advisor/recommendations/{id}/reject       — 사용자 거부 + 사유
POST /api/advisor/intraday-check                    — 수동 장중 자문 트리거 (테스트용)

만료 처리: 매 요청 진입 시 expire_overdue_recommendations 호출 (best-effort).
실제 cron은 daily_pipeline trade phase 직전에 별도 호출.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import AdvisorRecommendation
from services.advisor.service import (
    approve_recommendation,
    expire_overdue_recommendations,
    reject_recommendation,
    run_intraday_check,
    run_morning_brief,
)

router = APIRouter()


# ──── Schemas ────


class AdvisorRecommendationOut(BaseModel):
    id: int
    rec_date: date
    rec_type: str
    symbol: str
    side: str
    entry_price: Decimal | None
    stop_price: Decimal | None
    target_1r: Decimal | None
    target_2r: Decimal | None
    qty: int | None
    confidence: Decimal | None
    reasoning_text: str | None
    status: str
    user_decision_at: datetime | None
    reject_reason: str | None
    expires_at: datetime
    trade_plan_id: int | None
    model_version: str | None
    prompt_version: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovePayload(BaseModel):
    amount_usd: float | None = Field(default=None, gt=0, le=1_000_000)


class RejectPayload(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class IntradayCheckPayload(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    trigger_reason: str = Field(default="manual", min_length=1, max_length=32)


class MorningBriefPayload(BaseModel):
    dry_run: bool = False
    notify_telegram: bool = True


# ──── Endpoints ────


@router.post("/morning-brief")
async def trigger_morning_brief(
    payload: MorningBriefPayload | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """장 시작 전 자문 1회 실행. 멱등 (같은 날 같은 종목은 ON CONFLICT DO NOTHING).

    daily_pipeline preopen이 호출. 수동 호출도 가능 (테스트용).
    """
    p = payload or MorningBriefPayload()
    target = date.today()
    await expire_overdue_recommendations(session)
    result = await run_morning_brief(
        session,
        target,
        notify_telegram=p.notify_telegram,
        dry_run=p.dry_run,
    )
    return result


@router.get("/recommendations/today", response_model=list[AdvisorRecommendationOut])
async def list_today_recommendations(
    session: AsyncSession = Depends(get_session),
) -> list[AdvisorRecommendation]:
    await expire_overdue_recommendations(session)
    today = date.today()
    stmt = (
        select(AdvisorRecommendation)
        .where(AdvisorRecommendation.rec_date == today)
        .order_by(
            AdvisorRecommendation.confidence.desc().nullslast(),
            AdvisorRecommendation.created_at.desc(),
        )
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("/recommendations", response_model=list[AdvisorRecommendationOut])
async def list_recommendations(
    days: int = Query(default=7, ge=1, le=90),
    status: str | None = Query(default=None, description="pending | approved | rejected | expired"),
    session: AsyncSession = Depends(get_session),
) -> list[AdvisorRecommendation]:
    from datetime import timedelta

    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(AdvisorRecommendation)
        .where(AdvisorRecommendation.rec_date >= cutoff)
        .order_by(AdvisorRecommendation.created_at.desc())
    )
    if status:
        stmt = stmt.where(AdvisorRecommendation.status == status)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/recommendations/{rec_id}", response_model=AdvisorRecommendationOut)
async def get_recommendation(
    rec_id: int,
    session: AsyncSession = Depends(get_session),
) -> AdvisorRecommendation:
    rec = await session.get(AdvisorRecommendation, rec_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"recommendation {rec_id} not found")
    return rec


@router.post("/recommendations/{rec_id}/approve")
async def approve(
    rec_id: int,
    payload: ApprovePayload | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = payload or ApprovePayload()
    try:
        return await approve_recommendation(
            session, rec_id, user_amount_usd=p.amount_usd
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/recommendations/{rec_id}/reject")
async def reject(
    rec_id: int,
    payload: RejectPayload,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await reject_recommendation(session, rec_id, payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/intraday-check")
async def trigger_intraday_check(
    payload: IntradayCheckPayload,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """장중 단일 종목 자문 수동 트리거. 자동화는 intraday_monitor (Phase 2)."""
    today = date.today()
    return await run_intraday_check(
        session, payload.symbol, today, payload.trigger_reason,
    )


@router.get("/metrics")
async def get_metrics(
    days: int = Query(default=7, ge=1, le=90),
    horizon: int = Query(default=5, description="1 | 5 | 10 거래일 outcome 지평"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """advisor 추천의 hit rate / Sharpe / avg R 메트릭."""
    from datetime import timedelta

    from services.advisor.evaluator import compute_metrics

    end = date.today()
    start = end - timedelta(days=days)
    metrics = await compute_metrics(
        session, period_start=start, period_end=end, horizon_days=horizon
    )
    return metrics.to_dict()


@router.post("/self-critique")
async def run_self_critique(
    write_file: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """주간 self-critique 수동 트리거. Sunday cron이 자동 호출."""
    from services.advisor.self_critique import run_weekly_critique

    return await run_weekly_critique(session, write_file=write_file)
