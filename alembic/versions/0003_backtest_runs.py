"""backtest_runs table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column(
            "strategy_params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_source", sa.String(16), nullable=False),
        sa.Column("starting_cash", sa.Numeric(20, 2), nullable=False),
        sa.Column("final_equity", sa.Numeric(20, 2), nullable=False),
        sa.Column("total_pnl", sa.Numeric(20, 2), nullable=False),
        sa.Column("total_fills", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_backtest_runs_strategy_name", "backtest_runs", ["strategy_name"])
    op.create_index("ix_backtest_runs_symbol", "backtest_runs", ["symbol"])
    op.create_index("ix_backtest_runs_created_at", "backtest_runs", ["created_at"])
    op.create_index(
        "ix_backtest_runs_strategy_symbol", "backtest_runs", ["strategy_name", "symbol"]
    )


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_strategy_symbol", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_created_at", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_symbol", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_strategy_name", table_name="backtest_runs")
    op.drop_table("backtest_runs")
