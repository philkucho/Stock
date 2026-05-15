"""전략 / 프리셋 조회 엔드포인트.

- GET /api/strategies/presets — 거장 스타일 프리셋 6개
- GET /api/strategies/registry — 사용 가능한 전략 클래스 (composite, sma_cross 등)
- 라이브 전략 시작/중지(POST /{id}/start, /{id}/stop)는 Webull 어댑터 완성 후.
"""

from __future__ import annotations

from fastapi import APIRouter

from strategies import STRATEGY_REGISTRY, list_presets

router = APIRouter()


@router.get("/presets")
async def get_presets() -> list[dict]:
    """거장 스타일 프리셋 — UI에서 원클릭 로드용."""
    return list_presets()


@router.get("/registry")
async def get_registry() -> list[dict]:
    """사용 가능한 전략 클래스 카탈로그."""
    return [
        {
            "name": name,
            "class": cls.__name__,
            "module": cls.__module__,
        }
        for name, cls in STRATEGY_REGISTRY.items()
    ]


@router.get("/")
async def list_strategies() -> list[dict]:
    """레거시 호환 — registry 와 동일."""
    return await get_registry()


@router.post("/{strategy_id}/start")
async def start_strategy(strategy_id: str) -> dict:
    # TODO Webull 어댑터 완성 후: NautilusTrader TradingNode에 동적 추가
    return {"strategy_id": strategy_id, "status": "not_implemented"}


@router.post("/{strategy_id}/stop")
async def stop_strategy(strategy_id: str) -> dict:
    return {"strategy_id": strategy_id, "status": "not_implemented"}
