"""trade_plans + trade_plan_outcomes (사용자 매매 plan 추적)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # trade_plans
    op.create_table(
        "trade_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("stop_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("target_1r", sa.Numeric(20, 4), nullable=False),
        sa.Column("target_2r", sa.Numeric(20, 4), nullable=False),
        sa.Column(
            "composite_score", sa.Numeric(8, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "score_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("sector", sa.String(48), nullable=True),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("risk_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("plan_date", "symbol", name="uq_trade_plan_date_sym"),
    )
    op.create_index("ix_trade_plans_symbol", "trade_plans", ["symbol"])
    op.create_index("ix_trade_plan_date", "trade_plans", ["plan_date"])

    # trade_plan_outcomes
    op.create_table(
        "trade_plan_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "trade_plan_id",
            sa.Integer(),
            sa.ForeignKey("trade_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("exit_date", sa.Date(), nullable=False),
        sa.Column("exit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("pct_return", sa.Numeric(8, 4), nullable=False),
        sa.Column("spy_pct_return", sa.Numeric(8, 4), nullable=False),
        sa.Column("alpha", sa.Numeric(8, 4), nullable=False),
        sa.Column(
            "realized_pnl_usd", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "hit_target_1r", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("hit_stop", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "trade_plan_id", "horizon_days", name="uq_trade_outcome_plan_horizon"
        ),
    )
    op.create_index(
        "ix_trade_plan_outcomes_trade_plan_id",
        "trade_plan_outcomes",
        ["trade_plan_id"],
    )
    op.create_index(
        "ix_trade_outcome_horizon_date",
        "trade_plan_outcomes",
        ["horizon_days", "exit_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trade_outcome_horizon_date", table_name="trade_plan_outcomes"
    )
    op.drop_index(
        "ix_trade_plan_outcomes_trade_plan_id", table_name="trade_plan_outcomes"
    )
    op.drop_table("trade_plan_outcomes")
    op.drop_index("ix_trade_plan_date", table_name="trade_plans")
    op.drop_index("ix_trade_plans_symbol", table_name="trade_plans")
    op.drop_table("trade_plans")
