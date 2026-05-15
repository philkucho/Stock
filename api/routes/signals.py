"""Signal Library 조회 엔드포인트."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from signals import SIGNAL_REGISTRY

router = APIRouter()


class SignalOut(BaseModel):
    name: str
    description: str
    category: str
    min_bars: int


@router.get("/", response_model=list[SignalOut])
async def list_signals() -> list[SignalOut]:
    """등록된 모든 시그널 메타데이터."""
    return [
        SignalOut(
            name=s.name,
            description=s.description,
            category=s.category,
            min_bars=s.min_bars,
        )
        for s in SIGNAL_REGISTRY.values()
    ]
