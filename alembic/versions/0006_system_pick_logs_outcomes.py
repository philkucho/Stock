"""system_pick_logs + pick_outcomes (3 시스템 비교 추적)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_pick_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system_id", sa.String(32), nullable=False),
        sa.Column("pick_date", sa.Date(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("score", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column(
            "score_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("sector", sa.String(48), nullable=True),
        sa.Column("strategy_tag", sa.String(8), nullable=False, server_default="swing"),
        sa.Column("entry_price", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "sim_capital_usd", sa.Numeric(10, 2), nullable=False, server_default="2000"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "system_id", "pick_date", "symbol", name="uq_pick_log_sys_date_sym"
        ),
    )
    op.create_index("ix_system_pick_logs_system_id", "system_pick_logs", ["system_id"])
    op.create_index("ix_system_pick_logs_symbol", "system_pick_logs", ["symbol"])
    op.create_index(
        "ix_pick_log_date_system", "system_pick_logs", ["pick_date", "system_id"]
    )

    op.create_table(
        "pick_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "pick_log_id",
            sa.Integer(),
            sa.ForeignKey("system_pick_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("exit_date", sa.Date(), nullable=False),
        sa.Column("exit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("pct_return", sa.Numeric(8, 4), nullable=False),
        sa.Column("spy_pct_return", sa.Numeric(8, 4), nullable=False),
        sa.Column("alpha", sa.Numeric(8, 4), nullable=False),
        sa.Column("win_simple", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("win_alpha", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "realized_pnl_usd", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column("notes", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "pick_log_id", "horizon_days", name="uq_outcome_pick_horizon"
        ),
    )
    op.create_index("ix_pick_outcomes_pick_log_id", "pick_outcomes", ["pick_log_id"])
    op.create_index(
        "ix_outcome_horizon_date", "pick_outcomes", ["horizon_days", "exit_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_outcome_horizon_date", table_name="pick_outcomes")
    op.drop_index("ix_pick_outcomes_pick_log_id", table_name="pick_outcomes")
    op.drop_table("pick_outcomes")
    op.drop_index("ix_pick_log_date_system", table_name="system_pick_logs")
    op.drop_index("ix_system_pick_logs_symbol", table_name="system_pick_logs")
    op.drop_index("ix_system_pick_logs_system_id", table_name="system_pick_logs")
    op.drop_table("system_pick_logs")
