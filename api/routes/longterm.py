"""중장기 Fidelity 추천 endpoints — alembic 0014, 2026-06-05.

GET  /api/longterm/current        — 최신 pick_month 의 picks
GET  /api/longterm/history/{m}    — 특정 pick_month
GET  /api/longterm/months         — 사용 가능한 pick_month 리스트
GET  /api/longterm/outcomes       — alpha tracking (21/63/126/252d)
POST /api/longterm/refresh        — 수동 재선정 (dry-run 옵션)
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import LongtermOutcome, LongtermPick

router = APIRouter()


class LongtermPickOut(BaseModel):
    id: int
    pick_month: date
    rank: int
    symbol: str
    sector: str | None
    composite_score: Decimal
    gate_results: dict[str, Any]
    score_breakdown: dict[str, Any]
    weight_pct: Decimal
    status: str
    fidelity_action: str
    prev_pick_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LongtermOutcomeOut(BaseModel):
    id: int
    pick_id: int
    symbol: str
    pick_month: date
    eval_date: date
    days_held: int
    pct_return: Decimal
    spy_pct_return: Decimal
    alpha: Decimal

    model_config = {"from_attributes": True}


class CurrentSummary(BaseModel):
    pick_month: date | None
    regime: str  # "ok" | "defensive" | "unknown"
    new_count: int
    hold_count: int
    exit_suggested_count: int
    exited_count: int
    last_refreshed_at: datetime | None
    picks: list[LongtermPickOut]


@router.get("/months", response_model=list[date])
async def list_months(session: AsyncSession = Depends(get_session)) -> list[date]:
    stmt = select(LongtermPick.pick_month).distinct().order_by(desc(LongtermPick.pick_month))
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/current", response_model=CurrentSummary)
async def get_current(session: AsyncSession = Depends(get_session)) -> CurrentSummary:
    # 최신 pick_month
    month_stmt = (
        select(LongtermPick.pick_month)
        .order_by(desc(LongtermPick.pick_month))
        .limit(1)
    )
    latest_month = (await session.execute(month_stmt)).scalar_one_or_none()

    if latest_month is None:
        return CurrentSummary(
            pick_month=None, regime="unknown",
            new_count=0, hold_count=0, exit_suggested_count=0, exited_count=0,
            last_refreshed_at=None, picks=[],
        )

    stmt = (
        select(LongtermPick)
        .where(LongtermPick.pick_month == latest_month)
        .order_by(LongtermPick.rank, LongtermPick.symbol)
    )
    picks = list((await session.execute(stmt)).scalars().all())

    new_c = sum(1 for p in picks if p.status == "new")
    hold_c = sum(1 for p in picks if p.status == "hold")
    es_c = sum(1 for p in picks if p.status == "exit_suggested")
    ex_c = sum(1 for p in picks if p.status == "exited")

    # regime: gate_results 확인 — 전부 빈 dict면 defensive 추정
    has_active = any(p.gate_results for p in picks if p.status in ("new", "hold"))
    regime = "ok" if has_active else "defensive"

    last_refreshed = max((p.created_at for p in picks), default=None)

    return CurrentSummary(
        pick_month=latest_month,
        regime=regime,
        new_count=new_c, hold_count=hold_c,
        exit_suggested_count=es_c, exited_count=ex_c,
        last_refreshed_at=last_refreshed,
        picks=[LongtermPickOut.model_validate(p) for p in picks],
    )


@router.get("/history/{pick_month}", response_model=list[LongtermPickOut])
async def get_history(
    pick_month: date, session: AsyncSession = Depends(get_session)
) -> list[LongtermPickOut]:
    stmt = (
        select(LongtermPick)
        .where(LongtermPick.pick_month == pick_month)
        .order_by(LongtermPick.rank, LongtermPick.symbol)
    )
    picks = list((await session.execute(stmt)).scalars().all())
    if not picks:
        raise HTTPException(404, f"No picks for {pick_month}")
    return [LongtermPickOut.model_validate(p) for p in picks]


@router.get("/outcomes")
async def get_outcomes(
    horizon: int = Query(21, description="21 / 63 / 126 / 252"),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """horizon별 alpha 분포 + 평균."""
    stmt = (
        select(LongtermOutcome, LongtermPick)
        .join(LongtermPick, LongtermOutcome.pick_id == LongtermPick.id)
        .where(LongtermOutcome.days_held == horizon)
        .order_by(desc(LongtermOutcome.eval_date))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    out_list = []
    for oc, pick in rows:
        out_list.append({
            "pick_month": pick.pick_month.isoformat(),
            "symbol": pick.symbol,
            "eval_date": oc.eval_date.isoformat(),
            "days_held": oc.days_held,
            "pct_return": float(oc.pct_return),
            "spy_pct_return": float(oc.spy_pct_return),
            "alpha": float(oc.alpha),
            "status_at_eval": oc.status_at_eval,
        })
    # aggregates
    if rows:
        alphas = [float(oc.alpha) for oc, _ in rows]
        wins = sum(1 for a in alphas if a > 0)
        avg_alpha = sum(alphas) / len(alphas)
    else:
        wins, avg_alpha = 0, 0.0
    return {
        "horizon_days": horizon,
        "count": len(rows),
        "win_alpha": wins,
        "win_rate_pct": round(wins / len(rows) * 100, 1) if rows else 0,
        "avg_alpha_pct": round(avg_alpha, 3),
        "outcomes": out_list,
    }


@router.post("/refresh")
async def refresh(
    target_date: date | None = None,
    dry_run: bool = True,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """수동 재선정 — 기본은 dry-run."""
    from scripts.longterm_monthly_pick import main as monthly_main

    if target_date is None:
        target_date = date.today()

    result = await monthly_main(target_date, dry_run=dry_run)
    return {
        "target_date": target_date.isoformat(),
        "dry_run": dry_run,
        "status": result.get("status"),
        "defensive": result.get("defensive"),
        "candidates_passed": result.get("candidates_passed"),
        "pick_count": len(result.get("picks", [])),
        "db_inserted": result.get("db_inserted", 0),
    }
