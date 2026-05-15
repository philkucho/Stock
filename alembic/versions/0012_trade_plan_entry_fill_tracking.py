"""trade_plans entry leg 부분 체결 추적 (1.1)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-14

1.1 부분 체결 후속:
- broker_order_ids[0] = 1차 bracket parent (BUY entry stop_limit)
- broker_order_ids[1] = 2차 bracket parent (BUY entry stop_limit, 같은 trigger)
- monitor가 parent 자체의 filled_qty도 동기화 → entry partial 정확 식별.

이전 0011은 SELL leg(target_1r/2r 익절) fill만 추적. ENTRY parent 부분 체결은 미처리.
이게 중요한 이유: holding qty == partial_qty일 때
  (a) entry partial (BUY 10 → 5 체결) vs
  (b) 1차 익절 후 잔여 (BUY 10 → 10 체결 → SELL 5 익절)
가 *같은 holding 값*이라 reconcile에서 구분 불가. entry_filled - sell_filled = expected_holding.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trade_plans",
        sa.Column("entry_filled_qty_1", sa.Integer, nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("entry_filled_qty_2", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_plans", "entry_filled_qty_2")
    op.drop_column("trade_plans", "entry_filled_qty_1")
