"""longterm_picks + longterm_outcomes — 중장기(3~12개월) Fidelity 추천 시스템.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-05

설계 ([[swing-mode-v1]] 후속):
  - swing/intraday와 분리된 시스템 (feedback decay horizon=5 hardcoded 회귀 방지)
  - Monthly rebalance, S&P 500 universe
  - Stage 2 trend template + RS percentile + 12mo momentum
  - 60/40 turnover cap, top 10 균등 가중
  - Fidelity 수동 발주용 (자동매매 X)

status flow:
  new          : 이번 달 신규 진입
  hold         : 이전 달부터 보유 유지
  exit_suggested: 11~20위로 밀려나거나 게이트 fail (HOLD/TRIM 권고)
  exited        : Top 20 밖으로 밀려남 (SELL 권고)

fidelity_action:
  BUY  : 신규 진입 (weight_pct 매수 가이드)
  HOLD : 그대로 보유
  TRIM : 부분 청산 권고
  SELL : 전량 청산 권고
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "longterm_picks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("pick_month", sa.Date, nullable=False),  # 월 첫 거래일
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("sector", sa.String(48)),
        sa.Column("composite_score", sa.Numeric(6, 2), nullable=False),
        sa.Column(
            "gate_results", JSONB, nullable=False, server_default="{}"
        ),  # {stage2: true, ma200: true, near_52w: true, adv: true}
        sa.Column(
            "score_breakdown", JSONB, nullable=False, server_default="{}"
        ),  # {rs_pct: 87, mom_12mo: 0.43, sma200_dist: 0.18, c_margin: 0.7}
        sa.Column(
            "weight_pct", sa.Numeric(5, 2), nullable=False, server_default="10.00"
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="new",
        ),  # new | hold | exit_suggested | exited
        sa.Column(
            "fidelity_action",
            sa.String(8),
            nullable=False,
            server_default="BUY",
        ),  # BUY | HOLD | TRIM | SELL
        sa.Column(
            "prev_pick_id",
            sa.BigInteger,
            sa.ForeignKey("longterm_picks.id", ondelete="SET NULL"),
        ),  # rebalance lineage
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("pick_month", "symbol", name="uq_longterm_pick_month_sym"),
        sa.Index("ix_longterm_picks_month", "pick_month"),
        sa.Index("ix_longterm_picks_status", "status"),
    )

    op.create_table(
        "longterm_outcomes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "pick_id",
            sa.BigInteger,
            sa.ForeignKey("longterm_picks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("eval_date", sa.Date, nullable=False),
        sa.Column("days_held", sa.Integer, nullable=False),  # 21 / 63 / 126 / 252
        sa.Column("pct_return", sa.Numeric(8, 4), nullable=False),
        sa.Column("spy_pct_return", sa.Numeric(8, 4), nullable=False),
        sa.Column("alpha", sa.Numeric(8, 4), nullable=False),
        sa.Column("mfe_pct", sa.Numeric(8, 4)),  # max favorable excursion
        sa.Column("mae_pct", sa.Numeric(8, 4)),  # max adverse excursion
        sa.Column("status_at_eval", sa.String(16)),  # 평가 시점 status snapshot
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "pick_id", "days_held", name="uq_longterm_outcome_pick_horizon"
        ),
        sa.Index("ix_longterm_outcomes_eval", "eval_date", "days_held"),
    )


def downgrade() -> None:
    op.drop_table("longterm_outcomes")
    op.drop_table("longterm_picks")
