"""strategy_assignments table + backtest_runs.params_hash unique

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. backtest_runs.params_hash (nullable for backward compat with existing rows)
    op.add_column(
        "backtest_runs",
        sa.Column("params_hash", sa.String(32), nullable=True),
    )
    op.create_unique_constraint(
        "uq_backtest_runs_params_hash", "backtest_runs", ["params_hash"]
    )

    # 2. strategy_assignments
    op.create_table(
        "strategy_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("preset_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column(
            "assigned_at",
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
    )
    op.create_index(
        "ix_strategy_assignments_symbol", "strategy_assignments", ["symbol"]
    )
    op.create_index(
        "ix_assignments_symbol_preset", "strategy_assignments", ["symbol", "preset_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_assignments_symbol_preset", table_name="strategy_assignments")
    op.drop_index("ix_strategy_assignments_symbol", table_name="strategy_assignments")
    op.drop_table("strategy_assignments")

    op.drop_constraint("uq_backtest_runs_params_hash", "backtest_runs", type_="unique")
    op.drop_column("backtest_runs", "params_hash")
