"""advisor_recommendations 테이블 — AI 자문 에이전트 추천 저장.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-14

Claude Opus 4.7 자문 에이전트가 산출하는 추천 (장 시작 전 morning + 장중 intraday)을
영속화. 사용자 Telegram 승인 게이트를 통과해야 trade_plan으로 변환되어 발주된다.

rec_type:
  - 'morning'         : 09:25 preopen 직전, 그 날 전체 진입 후보 batch
  - 'intraday_entry'  : 장중 신규 진입 제안 (watchlist 종목)
  - 'intraday_add'    : 장중 보유 종목에 추가매수 제안
  - 'intraday_exit'   : 장중 부분/전량 청산 제안

status flow:
  pending -> approved (사용자 승인) -> executed (trade_plan 발주됨)
          -> rejected (사용자 거부, reason 저장)
          -> expired (expires_at 경과, 자동 무효)

UNIQUE(rec_date, symbol, rec_type) — 같은 날 같은 종목에 대해 같은 유형 추천 중복 방지.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "advisor_recommendations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("rec_date", sa.Date, nullable=False),
        sa.Column("rec_type", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("side", sa.String(8), nullable=False, server_default="BUY"),
        sa.Column("entry_price", sa.Numeric(12, 4)),
        sa.Column("stop_price", sa.Numeric(12, 4)),
        sa.Column("target_1r", sa.Numeric(12, 4)),
        sa.Column("target_2r", sa.Numeric(12, 4)),
        sa.Column("qty", sa.Integer),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("reasoning_text", sa.Text),
        sa.Column("model_version", sa.String(32)),
        sa.Column("prompt_version", sa.String(16)),
        sa.Column("context_snapshot", JSONB, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("user_decision_at", sa.DateTime(timezone=True)),
        sa.Column("reject_reason", sa.Text),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "trade_plan_id",
            sa.Integer,
            sa.ForeignKey("trade_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("telegram_message_id", sa.BigInteger),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "rec_date", "symbol", "rec_type",
            name="uq_advisor_rec_date_symbol_type",
        ),
    )
    op.create_index(
        "ix_advisor_rec_date_status",
        "advisor_recommendations",
        ["rec_date", "status"],
    )
    op.create_index(
        "ix_advisor_rec_expires_pending",
        "advisor_recommendations",
        ["expires_at"],
        postgresql_where=sa.text("status='pending'"),
    )
    op.create_index(
        "ix_advisor_rec_telegram_msg",
        "advisor_recommendations",
        ["telegram_message_id"],
        postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_advisor_rec_telegram_msg", table_name="advisor_recommendations")
    op.drop_index("ix_advisor_rec_expires_pending", table_name="advisor_recommendations")
    op.drop_index("ix_advisor_rec_date_status", table_name="advisor_recommendations")
    op.drop_table("advisor_recommendations")
