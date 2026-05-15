"""trade_plans broker_order_ids + trade_plan_outcomes 2-tier 컬럼

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-08

2-Tier 부분 청산 도입:
- trade_plans.broker_order_ids JSONB nullable: 2개 BRACKET 주문 ID 영속 추적
- trade_plan_outcomes.hit_target_2r BOOL: 2차 목표 도달 여부
- trade_plan_outcomes.qty_sold_at_1r INT: 1차 청산 수량
- trade_plan_outcomes.qty_sold_at_2r INT: 2차 청산 수량 (또는 stop hit/horizon close 시 잔여)
- trade_plan_outcomes.partial_realized_pnl_usd NUMERIC: 부분 청산 합산 PnL
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # trade_plans: broker_order_ids JSONB
    op.add_column(
        "trade_plans",
        sa.Column(
            "broker_order_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # trade_plan_outcomes: 4개 컬럼 추가
    op.add_column(
        "trade_plan_outcomes",
        sa.Column(
            "hit_target_2r", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "trade_plan_outcomes",
        sa.Column(
            "qty_sold_at_1r", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "trade_plan_outcomes",
        sa.Column(
            "qty_sold_at_2r", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "trade_plan_outcomes",
        sa.Column(
            "partial_realized_pnl_usd",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("trade_plan_outcomes", "partial_realized_pnl_usd")
    op.drop_column("trade_plan_outcomes", "qty_sold_at_2r")
    op.drop_column("trade_plan_outcomes", "qty_sold_at_1r")
    op.drop_column("trade_plan_outcomes", "hit_target_2r")
    op.drop_column("trade_plans", "broker_order_ids")
