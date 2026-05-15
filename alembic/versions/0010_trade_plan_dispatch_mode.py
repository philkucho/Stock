"""trade_plans dispatch_mode — 발송 경로 분기 (user_fixed vs orb_auto)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-13

Hybrid dispatch:
- 'user_fixed': /trading 페이지에서 사용자가 직접 entry/stop/target 입력한 plan.
                run_trade()가 09:30 ET cron에서 입력값 그대로 발송.
- 'orb_auto':  스캐너 자동 watchlist (preopen 산출).
                run_confirm()가 09:45 ET cron에서 ORB+VWAP+RVOL 평가 후 발송.

기존 데이터는 모두 'orb_auto'로 분류 (기존 watchlist 흐름이 ORB 기반이었으므로).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trade_plans",
        sa.Column(
            "dispatch_mode",
            sa.String(20),
            nullable=False,
            server_default="orb_auto",
        ),
    )


def downgrade() -> None:
    op.drop_column("trade_plans", "dispatch_mode")
