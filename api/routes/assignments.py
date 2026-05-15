"""종목별 활성 전략 토글 (CRUD).

UI에서 매트릭스 셀 → "이 (종목, 프리셋) 조합 활성" 버튼 누르면 호출.
라이브 매매 단계에선 enabled=True인 assignment에 대해서만 NautilusTrader 노드가 전략 인스턴스 생성.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import StrategyAssignment

router = APIRouter()


class AssignmentOut(BaseModel):
    id: int
    symbol: str
    preset_key: str
    enabled: bool
    params: dict[str, Any]
    notes: str | None
    assigned_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssignmentIn(BaseModel):
    symbol: str
    preset_key: str
    enabled: bool = True
    params: dict[str, Any] = {}
    notes: str | None = None


@router.get("/", response_model=list[AssignmentOut])
async def list_assignments(
    symbol: str | None = None,
    enabled_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[StrategyAssignment]:
    stmt = select(StrategyAssignment).order_by(
        StrategyAssignment.symbol, StrategyAssignment.preset_key
    )
    if symbol:
        stmt = stmt.where(StrategyAssignment.symbol == symbol.upper())
    if enabled_only:
        stmt = stmt.where(StrategyAssignment.enabled.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@router.post("/", response_model=AssignmentOut)
async def create_or_update_assignment(
    payload: AssignmentIn,
    session: AsyncSession = Depends(get_session),
) -> StrategyAssignment:
    """동일 (symbol, preset_key)면 update, 아니면 insert (upsert)."""
    symbol_u = payload.symbol.upper()
    stmt = select(StrategyAssignment).where(
        StrategyAssignment.symbol == symbol_u,
        StrategyAssignment.preset_key == payload.preset_key,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    if existing is None:
        row = StrategyAssignment(
            symbol=symbol_u,
            preset_key=payload.preset_key,
            enabled=payload.enabled,
            params=payload.params,
            notes=payload.notes,
        )
        session.add(row)
    else:
        existing.enabled = payload.enabled
        existing.params = payload.params
        if payload.notes is not None:
            existing.notes = payload.notes
        row = existing

    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{assignment_id}")
async def delete_assignment(
    assignment_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    row = await session.get(StrategyAssignment, assignment_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Assignment {assignment_id} not found")
    await session.delete(row)
    await session.commit()
    return {"status": "deleted", "id": str(assignment_id)}
