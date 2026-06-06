"""포지션 / 주문 / 계좌 조회 엔드포인트 — Alpaca paper 라이브 조회.

자동매매 흐름(daily_pipeline)이 broker_order_ids만 trade_plans 행에 적재하고
orders/positions 테이블에는 row를 쓰지 않기 때문에, 사용자가 화면에서 보는
브로커 상태는 Alpaca API에서 직접 조회한다.

- BROKER_ADAPTER 환경변수로 어댑터 선택 (기본 alpaca)
- 어댑터 init 실패(자격증명 없음) → 503
- 어댑터 호출 실패 → 502 + 메시지 (frontend의 allSettled가 표시)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from api.db import async_session_factory
from api.db.models import TradePlan
from broker_adapter import get_adapter
from broker_adapter.base import BrokerAdapter

logger = logging.getLogger(__name__)
router = APIRouter()


class PositionOut(BaseModel):
    account: str
    symbol: str
    quantity: Decimal
    avg_price: Decimal
    # NOTE: Alpaca 보유 포지션은 unrealized만 제공. 기존 필드명 유지(프론트 호환).
    # 프론트 라벨은 "미실현 PnL"로 변경됨.
    realized_pnl: Decimal
    # 홈 대시보드용 — 현재가/평가금액/미실현 수익률(%)
    current_price: float | None = None
    market_value: float | None = None
    unrealized_pl_pct: float | None = None
    updated_at: datetime


class OrderOut(BaseModel):
    id: int  # broker_order_id 기반 deterministic id (UI key 용)
    client_order_id: str
    broker_order_id: str | None
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    # bracket entry / stop loss / take profit (nested=True 조회 시 parent + legs에서 추출)
    entry_price: Decimal | None
    stop_loss_price: Decimal | None
    take_profit_price: Decimal | None
    status: str
    strategy_id: str | None
    submitted_at: datetime | None
    created_at: datetime


def _open_adapter() -> BrokerAdapter:
    try:
        return get_adapter()
    except Exception as exc:
        logger.warning("broker adapter init failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"브로커 어댑터 초기화 실패: {exc}",
        )


def _stable_id(order_id: str) -> int:
    """broker_order_id → 32bit signed positive int (UI key 용, 충돌 사실상 없음)."""
    h = 0
    for ch in order_id:
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return h


@router.get("/account")
async def get_account() -> dict:
    """Alpaca paper 계좌 요약 — 잔액/buying_power/PDT 상태."""
    adapter = _open_adapter()
    try:
        acc = await adapter.get_account()
    except Exception as exc:
        logger.exception("get_account failed")
        raise HTTPException(502, f"Alpaca 계좌 조회 실패: {exc}")
    finally:
        await adapter.close()

    return {
        "account_id": acc.account_id,
        "status": acc.status,
        "balance_usd": acc.cash,
        "equity": acc.equity,
        "last_equity": acc.last_equity,
        "buying_power": acc.buying_power,
        "pattern_day_trader": acc.pattern_day_trader,
        "trading_blocked": acc.trading_blocked,
        "daily_pnl_pct": acc.daily_pnl_pct,
    }


@router.get("/", response_model=list[PositionOut])
async def list_positions() -> list[PositionOut]:
    """현재 보유 포지션 (Alpaca paper 라이브)."""
    adapter = _open_adapter()
    try:
        positions = await adapter.get_positions()
    except Exception as exc:
        logger.exception("get_positions failed")
        raise HTTPException(502, f"Alpaca 포지션 조회 실패: {exc}")
    finally:
        await adapter.close()

    now = datetime.now(timezone.utc)
    return [
        PositionOut(
            account="alpaca-paper",
            symbol=p.symbol,
            quantity=Decimal(str(p.qty)),
            avg_price=Decimal(str(p.avg_entry_price)),
            realized_pnl=Decimal(str(p.unrealized_pl)),
            current_price=p.current_price,
            market_value=p.market_value,
            unrealized_pl_pct=p.unrealized_pl_pct,
            updated_at=now,
        )
        for p in positions
    ]


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    status: str = Query(default="all", pattern="^(open|closed|all)$"),
    limit: int = Query(default=100, le=500),
    system_only: bool = Query(
        default=True,
        description="True면 TradePlan.broker_order_ids에 기록된 우리 시스템 발송분만 반환. "
                    "False면 Alpaca 계좌의 모든 주문(테스트·수동 포함).",
    ),
) -> list[OrderOut]:
    """브로커 주문 (Alpaca paper 라이브). status=open|closed|all."""
    adapter = _open_adapter()
    try:
        orders = await adapter.get_orders(status=status, nested=True)  # type: ignore[arg-type]
    except Exception as exc:
        logger.exception("get_orders failed")
        raise HTTPException(502, f"Alpaca 주문 조회 실패: {exc}")
    finally:
        await adapter.close()

    if system_only:
        # TradePlan.broker_order_ids에 기록된 parent bracket id 집합과 교집합.
        async with async_session_factory() as s:
            rows = (
                await s.execute(
                    select(TradePlan.broker_order_ids).where(
                        TradePlan.broker_order_ids.is_not(None)
                    )
                )
            ).all()
        tracked: set[str] = set()
        for (bids,) in rows:
            if bids:
                tracked.update(str(b) for b in bids)
        orders = [o for o in orders if o.order_id in tracked]

    orders = orders[:limit]
    out: list[OrderOut] = []
    for o in orders:
        raw = o.raw or {}
        client_order_id = raw.get("client_order_id") or o.order_id
        created_at_raw = raw.get("created_at")
        if isinstance(created_at_raw, str):
            try:
                created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            except ValueError:
                created_at = o.submitted_at or datetime.now(timezone.utc)
        elif isinstance(created_at_raw, datetime):
            created_at = created_at_raw
        else:
            created_at = o.submitted_at or datetime.now(timezone.utc)

        out.append(
            OrderOut(
                id=_stable_id(o.order_id),
                client_order_id=str(client_order_id),
                broker_order_id=o.order_id,
                symbol=o.symbol,
                side=o.side,
                order_type=o.order_type,
                quantity=Decimal(str(o.qty)),
                entry_price=(
                    Decimal(str(o.entry_price)) if o.entry_price is not None else None
                ),
                stop_loss_price=(
                    Decimal(str(o.stop_loss_price)) if o.stop_loss_price is not None else None
                ),
                take_profit_price=(
                    Decimal(str(o.take_profit_price)) if o.take_profit_price is not None else None
                ),
                status=o.status,
                strategy_id=None,
                submitted_at=o.submitted_at,
                created_at=created_at,
            )
        )
    return out
