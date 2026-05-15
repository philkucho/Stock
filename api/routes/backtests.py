"""백테스트 결과 조회 엔드포인트.

CLI에서 `python -m backtests.run_sma_cross --save`로 적재된 결과를 조회.
실행 트리거 (POST /run)는 추후 작업.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import BacktestRun

router = APIRouter()


class BacktestRunOut(BaseModel):
    id: int
    strategy_name: str
    strategy_params: dict[str, Any]
    symbol: str
    interval: str
    period_start: datetime
    period_end: datetime
    data_source: str
    starting_cash: Decimal
    final_equity: Decimal
    total_pnl: Decimal
    total_fills: int
    total_positions: int
    wins: int
    losses: int
    win_rate: Decimal
    metrics: dict[str, Any] | None = None
    notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BacktestRunSummary(BaseModel):
    """목록 조회용 요약 (페이로드 작게)."""

    id: int
    strategy_name: str
    symbol: str
    interval: str
    period_start: datetime
    period_end: datetime
    total_pnl: Decimal
    win_rate: Decimal
    total_positions: int
    created_at: datetime

    model_config = {"from_attributes": True}


class BacktestListResponse(BaseModel):
    items: list[BacktestRunSummary]
    total: int
    limit: int
    offset: int


@router.get("/", response_model=BacktestListResponse)
async def list_backtests(
    symbol: str | None = Query(default=None, description="Filter by symbol"),
    strategy: str | None = Query(default=None, alias="strategy_name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> BacktestListResponse:
    base_filters = []
    if symbol:
        base_filters.append(BacktestRun.symbol == symbol.upper())
    if strategy:
        base_filters.append(BacktestRun.strategy_name == strategy)

    count_stmt = select(func.count()).select_from(BacktestRun)
    list_stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc())
    for f in base_filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(list_stmt.limit(limit).offset(offset))).scalars().all()

    return BacktestListResponse(
        items=[BacktestRunSummary.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", response_model=BacktestRunOut)
async def get_backtest(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> BacktestRun:
    row = await session.get(BacktestRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"BacktestRun {run_id} not found")
    return row
