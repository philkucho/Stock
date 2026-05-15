"""trade_plans intraday confirmation 컬럼 (ORB + VWAP + RVOL + confirm_status)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-10

5-Model Intraday Stack 도입:
- trade_plans.orb_high / orb_low: 09:30~09:44 ET opening range
- trade_plans.session_vwap: confirm 시점 누적 VWAP
- trade_plans.intraday_rvol: 첫 15분 RVOL (직전 20일 동시간대 median 대비)
- trade_plans.premarket_gap_pct: prev_close 대비 갭 %
- trade_plans.premarket_rvol: 프리마켓 RVOL
- trade_plans.confirm_status: 'watchlist' | 'passed' | 'failed' | 'sent' | 'skipped'

기존 entry/stop/target_1r/target_2r 컬럼은 그대로 사용 — preopen에서 provisional,
confirm phase 에서 ORB 기반으로 덮어씀.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trade_plans",
        sa.Column("orb_high", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("orb_low", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("session_vwap", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("intraday_rvol", sa.Numeric(8, 3), nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("premarket_gap_pct", sa.Numeric(8, 3), nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column("premarket_rvol", sa.Numeric(8, 3), nullable=True),
    )
    op.add_column(
        "trade_plans",
        sa.Column(
            "confirm_status",
            sa.String(20),
            nullable=False,
            server_default="watchlist",
        ),
    )


def downgrade() -> None:
    op.drop_column("trade_plans", "confirm_status")
    op.drop_column("trade_plans", "premarket_rvol")
    op.drop_column("trade_plans", "premarket_gap_pct")
    op.drop_column("trade_plans", "intraday_rvol")
    op.drop_column("trade_plans", "session_vwap")
    op.drop_column("trade_plans", "orb_low")
    op.drop_column("trade_plans", "orb_high")
