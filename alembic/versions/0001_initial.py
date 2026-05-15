"""initial schema: orders, fills, positions

Revision ID: 0001
Revises:
Create Date: 2026-05-05

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    """Reference an existing enum without re-creating it inside create_table."""
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    postgresql.ENUM("BUY", "SELL", name="order_side").create(bind, checkfirst=True)
    postgresql.ENUM("MARKET", "LIMIT", "STOP", "STOP_LIMIT", name="order_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(
        "PENDING",
        "SUBMITTED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
        name="order_status",
    ).create(bind, checkfirst=True)

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", _enum("order_side", "BUY", "SELL"), nullable=False),
        sa.Column(
            "order_type",
            _enum("order_type", "MARKET", "LIMIT", "STOP", "STOP_LIMIT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("limit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_price", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "status",
            _enum(
                "order_status",
                "PENDING",
                "SUBMITTED",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELED",
                "REJECTED",
                "EXPIRED",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index("ix_orders_client_order_id", "orders", ["client_order_id"], unique=True)
    op.create_index("ix_orders_broker_order_id", "orders", ["broker_order_id"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_strategy_id", "orders", ["strategy_id"])

    op.create_table(
        "fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("broker_fill_id", sa.String(64), nullable=True, unique=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", _enum("order_side", "BUY", "SELL"), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("fee", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"])
    op.create_index("ix_fills_symbol", "fills", ["symbol"])
    op.create_index("ix_fills_executed_at", "fills", ["executed_at"])

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_positions_symbol_account", "positions", ["symbol", "account"])


def downgrade() -> None:
    op.drop_index("ix_positions_symbol_account", table_name="positions")
    op.drop_table("positions")

    op.drop_index("ix_fills_executed_at", table_name="fills")
    op.drop_index("ix_fills_symbol", table_name="fills")
    op.drop_index("ix_fills_order_id", table_name="fills")
    op.drop_table("fills")

    op.drop_index("ix_orders_strategy_id", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_symbol", table_name="orders")
    op.drop_index("ix_orders_broker_order_id", table_name="orders")
    op.drop_index("ix_orders_client_order_id", table_name="orders")
    op.drop_table("orders")

    bind = op.get_bind()
    postgresql.ENUM(name="order_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="order_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="order_side").drop(bind, checkfirst=True)
