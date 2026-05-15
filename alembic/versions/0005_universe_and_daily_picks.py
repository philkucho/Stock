"""universe_members + daily_picks tables (단타 스캐너 Stage 1/2)

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Stage 1: universe_members
    op.create_table(
        "universe_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("base_score", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_revalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.UniqueConstraint("symbol", "source", name="uq_universe_symbol_source"),
    )
    op.create_index("ix_universe_members_symbol", "universe_members", ["symbol"])
    op.create_index(
        "ix_universe_enabled_valid", "universe_members", ["enabled", "valid_until"]
    )

    # Stage 2: daily_picks
    op.create_table(
        "daily_picks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pick_date", sa.Date(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column(
            "is_backup",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("total_score", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "gate_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "score_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("pivot_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("stop_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("target_1r", sa.Numeric(20, 4), nullable=False),
        sa.Column("target_2r", sa.Numeric(20, 4), nullable=False),
        sa.Column("risk_per_share", sa.Numeric(20, 4), nullable=False),
        sa.Column("position_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strategy_tag", sa.String(8), nullable=False, server_default="day"),
        sa.Column("catalyst_summary", sa.String(500), nullable=True),
        sa.Column("catalyst_source", sa.String(64), nullable=True),
        sa.Column(
            "market_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("sector", sa.String(48), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("pick_date", "symbol", name="uq_daily_picks_date_symbol"),
    )
    op.create_index("ix_daily_picks_pick_date", "daily_picks", ["pick_date"])
    op.create_index("ix_daily_picks_symbol", "daily_picks", ["symbol"])
    op.create_index("ix_daily_picks_date_rank", "daily_picks", ["pick_date", "rank"])


def downgrade() -> None:
    op.drop_index("ix_daily_picks_date_rank", table_name="daily_picks")
    op.drop_index("ix_daily_picks_symbol", table_name="daily_picks")
    op.drop_index("ix_daily_picks_pick_date", table_name="daily_picks")
    op.drop_table("daily_picks")

    op.drop_index("ix_universe_enabled_valid", table_name="universe_members")
    op.drop_index("ix_universe_members_symbol", table_name="universe_members")
    op.drop_table("universe_members")
