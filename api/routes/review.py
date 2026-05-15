"""Daily Review — 계획(trade_plans) vs 실제(outcomes) 비교 리포트.

GET /api/review/{date}        — 특정 일자 리뷰
GET /api/review/today         — 오늘 리뷰 (alias)

사용자가 EOD에 single page로 모든 정보를 보도록 설계.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db import get_session
from api.db.models import TradePlan

router = APIRouter()


class ReviewPlanRow(BaseModel):
    rank: int
    symbol: str
    sector: str | None
    system_source: str  # "intraday_v1" | "v10" | "v9_fallback"
    confirm_status: str  # "watchlist" | "passed" | "failed" | "sent" | "skipped"
    composite_score: float

    # 5-Model 신호 메타 (preopen 시점)
    premarket_gap_pct: float | None
    premarket_rvol: float | None
    catalyst_kind: str | None
    catalyst_summary: str | None

    # ORB 평가 (confirm 시점)
    orb_high: float | None
    orb_low: float | None
    session_vwap: float | None
    intraday_rvol: float | None
    fail_reasons: list[str] = Field(default_factory=list)

    # 계획 (preopen + confirm 산출)
    planned_entry: float
    planned_stop: float
    planned_target_1r: float
    planned_target_2r: float
    planned_shares: int
    planned_amount_usd: float
    planned_risk_usd: float

    # 실제 (outcomes — 1d horizon 기준)
    actual_exit_price: float | None
    actual_pct_return: float | None
    actual_alpha: float | None
    actual_realized_pnl: float | None
    hit_target_1r: bool = False
    hit_target_2r: bool = False
    hit_stop: bool = False
    qty_sold_at_1r: int = 0
    qty_sold_at_2r: int = 0


class ReviewSummary(BaseModel):
    watchlist_count: int  # 단타 5-Model 산출
    passed_count: int     # ORB 4-pass 통과
    failed_count: int     # ORB fail
    sent_count: int       # bracket 주문 발송
    skipped_count: int    # 발송 시점 skip (BP 등)
    fail_reason_counts: dict[str, int] = Field(default_factory=dict)


class ReviewTotals(BaseModel):
    planned_exposure_usd: float
    planned_risk_usd: float
    actual_realized_pnl_usd: float
    actual_alpha_avg: float | None  # alpha 단순평균 (발송 종목 한정)
    win_count: int  # alpha > 0
    loss_count: int  # alpha < 0


class DailyReviewResponse(BaseModel):
    review_date: date
    summary: ReviewSummary
    totals: ReviewTotals
    plans: list[ReviewPlanRow]


def _resolve_system_source(meta: dict[str, Any] | None) -> str:
    if not isinstance(meta, dict):
        return "v10"
    if meta.get("version") == "intraday_v1":
        return "intraday_v1"
    if meta.get("source") == "v9_fallback":
        return "v9_fallback"
    return "v10"


def _plan_to_row(plan: TradePlan) -> ReviewPlanRow:
    meta = plan.score_meta or {}
    fail_reasons = meta.get("confirm_fail_reasons") or []
    if not isinstance(fail_reasons, list):
        fail_reasons = []

    # 1d outcome 선택 (단타는 1d가 가장 관련 — EOD 청산이라 사실상 final)
    outcome_1d = next(
        (o for o in plan.outcomes if o.horizon_days == 1), None
    )

    return ReviewPlanRow(
        rank=plan.rank,
        symbol=plan.symbol,
        sector=plan.sector,
        system_source=_resolve_system_source(meta),
        confirm_status=plan.confirm_status,
        composite_score=float(plan.composite_score),
        premarket_gap_pct=(
            float(plan.premarket_gap_pct) if plan.premarket_gap_pct is not None else None
        ),
        premarket_rvol=(
            float(plan.premarket_rvol) if plan.premarket_rvol is not None else None
        ),
        catalyst_kind=meta.get("catalyst_kind"),
        catalyst_summary=meta.get("catalyst_summary"),
        orb_high=float(plan.orb_high) if plan.orb_high is not None else None,
        orb_low=float(plan.orb_low) if plan.orb_low is not None else None,
        session_vwap=(
            float(plan.session_vwap) if plan.session_vwap is not None else None
        ),
        intraday_rvol=(
            float(plan.intraday_rvol) if plan.intraday_rvol is not None else None
        ),
        fail_reasons=list(fail_reasons),
        planned_entry=float(plan.entry_price),
        planned_stop=float(plan.stop_price),
        planned_target_1r=float(plan.target_1r),
        planned_target_2r=float(plan.target_2r),
        planned_shares=plan.shares,
        planned_amount_usd=float(plan.amount_usd),
        planned_risk_usd=float(plan.risk_usd),
        actual_exit_price=(
            float(outcome_1d.exit_price) if outcome_1d else None
        ),
        actual_pct_return=(
            float(outcome_1d.pct_return) if outcome_1d else None
        ),
        actual_alpha=(
            float(outcome_1d.alpha) if outcome_1d else None
        ),
        actual_realized_pnl=(
            float(outcome_1d.realized_pnl_usd) if outcome_1d else None
        ),
        hit_target_1r=bool(outcome_1d.hit_target_1r) if outcome_1d else False,
        hit_target_2r=bool(outcome_1d.hit_target_2r) if outcome_1d else False,
        hit_stop=bool(outcome_1d.hit_stop) if outcome_1d else False,
        qty_sold_at_1r=int(outcome_1d.qty_sold_at_1r) if outcome_1d else 0,
        qty_sold_at_2r=int(outcome_1d.qty_sold_at_2r) if outcome_1d else 0,
    )


@router.get("/today", response_model=DailyReviewResponse)
async def get_today_review(
    session: AsyncSession = Depends(get_session),
) -> DailyReviewResponse:
    return await _build_review(date.today(), session)


@router.get("/{review_date}", response_model=DailyReviewResponse)
async def get_review_by_date(
    review_date: date,
    session: AsyncSession = Depends(get_session),
) -> DailyReviewResponse:
    return await _build_review(review_date, session)


async def _build_review(
    review_date: date, session: AsyncSession
) -> DailyReviewResponse:
    stmt = (
        select(TradePlan)
        .options(selectinload(TradePlan.outcomes))
        .where(TradePlan.plan_date == review_date)
        .order_by(TradePlan.rank)
    )
    plans = list((await session.execute(stmt)).scalars().all())
    rows = [_plan_to_row(p) for p in plans]

    # Summary counters
    status_counts = Counter(r.confirm_status for r in rows)
    fail_reason_counter: Counter[str] = Counter()
    for r in rows:
        for reason in r.fail_reasons:
            # 메시지에서 첫 단어만 그룹핑 (예: "rvol 1.12x < 1.5x" → "rvol")
            key = reason.split()[0] if reason else "unknown"
            fail_reason_counter[key] += 1

    summary = ReviewSummary(
        watchlist_count=len(rows),
        passed_count=status_counts.get("passed", 0),
        failed_count=status_counts.get("failed", 0),
        sent_count=status_counts.get("sent", 0),
        skipped_count=status_counts.get("skipped", 0),
        fail_reason_counts=dict(fail_reason_counter),
    )

    # Totals — 발송 종목 한정으로 PnL 합산. 계획은 모든 plan.
    planned_exposure = sum(r.planned_amount_usd for r in rows)
    planned_risk = sum(r.planned_risk_usd for r in rows)

    sent_with_outcome = [
        r for r in rows
        if r.confirm_status == "sent" and r.actual_alpha is not None
    ]
    realized_pnl = sum(
        (r.actual_realized_pnl or 0.0) for r in sent_with_outcome
    )
    alphas = [r.actual_alpha for r in sent_with_outcome if r.actual_alpha is not None]
    alpha_avg = (sum(alphas) / len(alphas)) if alphas else None
    win_n = sum(1 for a in alphas if a > 0)
    loss_n = sum(1 for a in alphas if a < 0)

    totals = ReviewTotals(
        planned_exposure_usd=round(planned_exposure, 2),
        planned_risk_usd=round(planned_risk, 2),
        actual_realized_pnl_usd=round(realized_pnl, 2),
        actual_alpha_avg=round(alpha_avg, 4) if alpha_avg is not None else None,
        win_count=win_n,
        loss_count=loss_n,
    )

    return DailyReviewResponse(
        review_date=review_date,
        summary=summary,
        totals=totals,
        plans=rows,
    )
