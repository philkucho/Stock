"""bars hypertable + timescaledb extension

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # TimescaleDB extension 활성화 (timescale/timescaledb 이미지에 사전 설치됨)
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "bars",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=False),
        sa.Column("high", sa.Numeric(20, 8), nullable=False),
        sa.Column("low", sa.Numeric(20, 8), nullable=False),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(20, 4), nullable=False),
        sa.Column("source", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("time", "symbol", "interval", name="bars_pkey"),
    )
    op.create_index("ix_bars_symbol_time", "bars", ["symbol", "time"])

    # Hypertable 변환: time 컬럼 기준 7일 단위 청크
    op.execute(
        "SELECT create_hypertable('bars', 'time', "
        "chunk_time_interval => INTERVAL '7 days', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.drop_index("ix_bars_symbol_time", table_name="bars")
    op.drop_table("bars")
    # TimescaleDB extension은 다른 hypertable이 있을 수 있어 drop 안 함
