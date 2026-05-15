"""trade_plans 부분 청산 진행도 추적 컬럼 (1차/2차 별도 fill qty + avg price)

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-14

2.2 부분 청산 동기화:
- broker_order_ids[0] = 1차 bracket parent (target_1r 익절 leg 포함)
- broker_order_ids[1] = 2차 bracket parent (target_2r 익절 leg 포함)
- monitor가 매 15분마다 children 상태 조회 → 1차/2차 sell leg의 cumulative
  filled_qty + avg_fill_price를 plan에 동기화.

기존 Fill 테이블(0001 migration)이 있긴 하나, plan 단위로 진행도 한눈에 보기 위한
denormalized 컬럼. monitor가 idempotent하게 갱신.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trade_plans",
        sa.Column("filled_qty_1r", sa.Integer, nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("filled_qty_2r", sa.Integer, nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("filled_avg_price_1r", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("filled_avg_price_2r", sa.Numeric(20, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_plans", "filled_avg_price_2r")
    op.drop_column("trade_plans", "filled_avg_price_1r")
    op.drop_column("trade_plans", "filled_qty_2r")
    op.drop_column("trade_plans", "filled_qty_1r")
