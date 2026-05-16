"""Dispatch source 별 (user_fixed / orb_auto / advisor) 실거래 성과 비교.

3가지 신규 진입 경로:
  - user_fixed : /trading UI 사용자 입력 → 09:30 cron run_trade 발송
  - orb_auto   : 09:25 preopen watchlist → 09:45 cron run_confirm (ORB 4-pass) 발송
  - advisor    : AI 자문 (Gemini) → Telegram approve → TradePlan upsert + 즉시 발송

dispatch_mode 컬럼만으론 advisor를 user_fixed와 구분 불가
(advisor 승인은 dispatch_mode='user_fixed'로 plan을 만들기 때문).
→ AdvisorRecommendation.trade_plan_id 조인으로 advisor 경로 식별.

GET /api/sources/summary?days=30
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db import get_session
from api.db.models import AdvisorRecommendation, TradePlan, TradePlanOutcome

router = APIRouter()


SourcePath = Literal["user_fixed", "orb_auto", "advisor"]
DEFAULT_HORIZON = 1  # 1d outcome 기준 summary (5d/10d는 trade-level에서 별도 표시 가능)


class PathSummary(BaseModel):
    n_plans: int             # 전체 plan 수 (status 무관)
    n_sent: int              # 실제 발송 (broker_order_ids 있음)
    n_with_outcome: int      # outcome backfill 완료
    win_rate: float          # outcome 있는 것 중 pct_return > 0 비율
    avg_return_pct: float
    avg_alpha_pct: float
    total_pnl_usd: float
    hit_t1_rate: float
    hit_t2_rate: float
    hit_stop_rate: float


class TradeRow(BaseModel):
    plan_id: int
    plan_date: date
    symbol: str
    path: SourcePath
    confirm_status: str
    qty: int
    entry_price: Decimal
    stop_price: Decimal
    target_1r: Decimal
    target_2r: Decimal
    sector: str | None
    # outcome (없으면 null)
    horizon_days: int | None
    exit_date: date | None
    exit_price: Decimal | None
    pct_return: float | None
    alpha_pct: float | None
    realized_pnl_usd: float | None
    partial_pnl_usd: float | None
    hit_target_1r: bool | None
    hit_target_2r: bool | None
    hit_stop: bool | None
    has_broker_orders: bool


class CumulativePoint(BaseModel):
    date: date
    user_fixed: float
    orb_auto: float
    advisor: float


class SourcesSummaryResponse(BaseModel):
    range_start: date
    range_end: date
    summary: dict[SourcePath, PathSummary]
    cumulative_pnl: list[CumulativePoint]
    trades: list[TradeRow]


def _attribute_path(
    plan: TradePlan, advisor_linked_ids: set[int]
) -> SourcePath:
    """plan을 dispatch path 1개로 귀속."""
    if plan.id in advisor_linked_ids:
        return "advisor"
    if plan.dispatch_mode == "orb_auto":
        return "orb_auto"
    return "user_fixed"


def _select_outcome(
    outcomes: list[TradePlanOutcome], preferred_horizon: int
) -> TradePlanOutcome | None:
    """preferred horizon 우선, 없으면 가장 짧은 horizon."""
    if not outcomes:
        return None
    for o in outcomes:
        if o.horizon_days == preferred_horizon:
            return o
    return min(outcomes, key=lambda o: o.horizon_days)


@router.get("/summary", response_model=SourcesSummaryResponse)
async def get_sources_summary(
    days: int = Query(30, ge=1, le=365),
    horizon: int = Query(DEFAULT_HORIZON, ge=1, le=10),
    session: AsyncSession = Depends(get_session),
) -> SourcesSummaryResponse:
    """N일 lookback (plan_date 기준) dispatch path별 성과 요약 + trade list."""
    today = date.today()
    cutoff = today - timedelta(days=days)

    # 1) plans + outcomes 일괄 fetch
    stmt = (
        select(TradePlan)
        .where(TradePlan.plan_date >= cutoff)
        .where(TradePlan.plan_date <= today)
        .options(selectinload(TradePlan.outcomes))
        .order_by(TradePlan.plan_date.desc(), TradePlan.created_at.desc())
    )
    plans = list((await session.execute(stmt)).scalars().all())

    # 2) advisor 연결된 plan_id set
    adv_stmt = select(AdvisorRecommendation.trade_plan_id).where(
        AdvisorRecommendation.trade_plan_id.is_not(None),
        AdvisorRecommendation.status.in_(("approved", "executed")),
        AdvisorRecommendation.rec_date >= cutoff,
    )
    adv_ids = {
        row[0] for row in (await session.execute(adv_stmt)).all() if row[0] is not None
    }

    # 3) trade rows + per-path aggregates
    paths: list[SourcePath] = ["user_fixed", "orb_auto", "advisor"]
    agg: dict[SourcePath, dict[str, float]] = {
        p: {
            "n_plans": 0, "n_sent": 0, "n_with_outcome": 0,
            "sum_return": 0.0, "sum_alpha": 0.0, "sum_pnl": 0.0,
            "wins": 0, "t1": 0, "t2": 0, "stop": 0,
        } for p in paths
    }
    # cumulative PnL: 날짜별 path별 partial_pnl 합계
    daily_pnl: dict[date, dict[SourcePath, float]] = defaultdict(
        lambda: {"user_fixed": 0.0, "orb_auto": 0.0, "advisor": 0.0}
    )
    trade_rows: list[TradeRow] = []

    for p in plans:
        path = _attribute_path(p, adv_ids)
        outcome = _select_outcome(list(p.outcomes), horizon)
        has_broker = bool(p.broker_order_ids)

        agg[path]["n_plans"] += 1
        if has_broker:
            agg[path]["n_sent"] += 1
        if outcome is not None:
            agg[path]["n_with_outcome"] += 1
            ret = float(outcome.pct_return)
            alpha = float(outcome.alpha)
            partial = float(outcome.partial_realized_pnl_usd or 0)
            agg[path]["sum_return"] += ret
            agg[path]["sum_alpha"] += alpha
            agg[path]["sum_pnl"] += partial
            if ret > 0:
                agg[path]["wins"] += 1
            if outcome.hit_target_1r:
                agg[path]["t1"] += 1
            if outcome.hit_target_2r:
                agg[path]["t2"] += 1
            if outcome.hit_stop:
                agg[path]["stop"] += 1
            # cumulative chart에는 partial PnL 사용 (2-tier 부분 청산 반영)
            daily_pnl[outcome.exit_date][path] += partial

        trade_rows.append(TradeRow(
            plan_id=p.id,
            plan_date=p.plan_date,
            symbol=p.symbol,
            path=path,
            confirm_status=p.confirm_status,
            qty=int(p.shares),
            entry_price=p.entry_price,
            stop_price=p.stop_price,
            target_1r=p.target_1r,
            target_2r=p.target_2r,
            sector=p.sector,
            horizon_days=outcome.horizon_days if outcome else None,
            exit_date=outcome.exit_date if outcome else None,
            exit_price=outcome.exit_price if outcome else None,
            pct_return=float(outcome.pct_return) if outcome else None,
            alpha_pct=float(outcome.alpha) if outcome else None,
            realized_pnl_usd=float(outcome.realized_pnl_usd) if outcome else None,
            partial_pnl_usd=float(outcome.partial_realized_pnl_usd) if outcome else None,
            hit_target_1r=outcome.hit_target_1r if outcome else None,
            hit_target_2r=outcome.hit_target_2r if outcome else None,
            hit_stop=outcome.hit_stop if outcome else None,
            has_broker_orders=has_broker,
        ))

    # 4) per-path summary 마무리 (rate 계산)
    summary: dict[SourcePath, PathSummary] = {}
    for path in paths:
        a = agg[path]
        n_out = a["n_with_outcome"]
        denom = max(n_out, 1)
        summary[path] = PathSummary(
            n_plans=int(a["n_plans"]),
            n_sent=int(a["n_sent"]),
            n_with_outcome=int(n_out),
            win_rate=round(a["wins"] / denom, 4) if n_out else 0.0,
            avg_return_pct=round(a["sum_return"] / denom, 4) if n_out else 0.0,
            avg_alpha_pct=round(a["sum_alpha"] / denom, 4) if n_out else 0.0,
            total_pnl_usd=round(a["sum_pnl"], 2),
            hit_t1_rate=round(a["t1"] / denom, 4) if n_out else 0.0,
            hit_t2_rate=round(a["t2"] / denom, 4) if n_out else 0.0,
            hit_stop_rate=round(a["stop"] / denom, 4) if n_out else 0.0,
        )

    # 5) cumulative PnL series (date 정렬 + 누적 합)
    sorted_dates = sorted(daily_pnl.keys())
    cum_user = cum_orb = cum_adv = 0.0
    cumulative: list[CumulativePoint] = []
    for d in sorted_dates:
        cum_user += daily_pnl[d]["user_fixed"]
        cum_orb += daily_pnl[d]["orb_auto"]
        cum_adv += daily_pnl[d]["advisor"]
        cumulative.append(CumulativePoint(
            date=d,
            user_fixed=round(cum_user, 2),
            orb_auto=round(cum_orb, 2),
            advisor=round(cum_adv, 2),
        ))

    return SourcesSummaryResponse(
        range_start=cutoff,
        range_end=today,
        summary=summary,
        cumulative_pnl=cumulative,
        trades=trade_rows,
    )
