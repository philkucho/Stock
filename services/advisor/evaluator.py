"""AI 자문 추천 평가 — hit rate / avg R / Sharpe.

trade_plan_outcomes에 매칭된 approved 추천만 대상.
rejected는 counterfactual 분석용 (별도 함수).

매일 16:30 backfill 직후 또는 주간 cron으로 호출.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.models import AdvisorRecommendation, TradePlan, TradePlanOutcome


@dataclass
class AdvisorMetrics:
    period_start: date
    period_end: date
    total: int
    approved: int
    rejected: int
    expired: int
    executed_with_outcome: int
    win_count: int = 0
    win_rate: float = 0.0
    avg_pct_return: float = 0.0
    avg_alpha: float = 0.0
    avg_realized_r: float = 0.0  # realized pct / risk pct
    sharpe_approx: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_recommendations": self.total,
            "approved": self.approved,
            "rejected": self.rejected,
            "expired": self.expired,
            "executed_with_outcome": self.executed_with_outcome,
            "win_count": self.win_count,
            "win_rate_pct": round(self.win_rate * 100, 2),
            "avg_pct_return": round(self.avg_pct_return, 3),
            "avg_alpha": round(self.avg_alpha, 3),
            "avg_realized_r": round(self.avg_realized_r, 3),
            "sharpe_approx": round(self.sharpe_approx, 3),
        }


async def compute_metrics(
    session: AsyncSession,
    *,
    period_start: date,
    period_end: date,
    horizon_days: int = 5,
) -> AdvisorMetrics:
    """기간 내 추천의 outcome 매칭 + 메트릭 산출.

    horizon_days: trade_plan_outcomes 중 어느 지평을 쓸지 (1/5/10).
    """
    # 1) 기간 내 advisor_recommendations
    stmt = (
        select(AdvisorRecommendation)
        .where(AdvisorRecommendation.rec_date >= period_start)
        .where(AdvisorRecommendation.rec_date <= period_end)
    )
    recs = list((await session.execute(stmt)).scalars().all())

    total = len(recs)
    approved = sum(1 for r in recs if r.status == "approved" or r.status == "executed")
    rejected = sum(1 for r in recs if r.status == "rejected")
    expired = sum(1 for r in recs if r.status == "expired")

    # 2) approved 중 trade_plan_id 있는 것 → outcome 조회
    plan_ids = [r.trade_plan_id for r in recs if r.trade_plan_id]
    outcomes_by_plan: dict[int, TradePlanOutcome] = {}
    if plan_ids:
        stmt_p = (
            select(TradePlan)
            .options(selectinload(TradePlan.outcomes))
            .where(TradePlan.id.in_(plan_ids))
        )
        for plan in (await session.execute(stmt_p)).scalars().all():
            for outcome in plan.outcomes:
                if outcome.horizon_days == horizon_days:
                    outcomes_by_plan[plan.id] = outcome
                    break

    pct_returns: list[float] = []
    alphas: list[float] = []
    realized_rs: list[float] = []
    wins = 0
    for rec in recs:
        if not rec.trade_plan_id:
            continue
        out = outcomes_by_plan.get(rec.trade_plan_id)
        if out is None:
            continue
        pct = float(out.pct_return)
        alpha = float(out.alpha)
        pct_returns.append(pct)
        alphas.append(alpha)
        if out.win_simple:
            wins += 1
        # realized R = pct_return / risk_pct
        if rec.entry_price and rec.stop_price:
            entry = float(rec.entry_price)
            stop = float(rec.stop_price)
            risk_pct = (entry - stop) / entry * 100 if entry > 0 else 0
            if risk_pct > 0:
                realized_rs.append(pct / risk_pct)

    n = len(pct_returns)
    avg_pct = sum(pct_returns) / n if n else 0.0
    avg_alpha = sum(alphas) / n if n else 0.0
    avg_r = sum(realized_rs) / len(realized_rs) if realized_rs else 0.0
    win_rate = wins / n if n else 0.0

    # Sharpe approximation (no risk-free): mean / std
    sharpe = 0.0
    if n >= 2:
        mean = avg_pct
        var = sum((p - mean) ** 2 for p in pct_returns) / (n - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std > 0:
            sharpe = mean / std

    return AdvisorMetrics(
        period_start=period_start,
        period_end=period_end,
        total=total,
        approved=approved,
        rejected=rejected,
        expired=expired,
        executed_with_outcome=n,
        win_count=wins,
        win_rate=win_rate,
        avg_pct_return=avg_pct,
        avg_alpha=avg_alpha,
        avg_realized_r=avg_r,
        sharpe_approx=sharpe,
    )


async def compute_weekly(session: AsyncSession, week_end: date | None = None) -> AdvisorMetrics:
    """기준일로부터 지난 7일."""
    end = week_end or date.today()
    start = end - timedelta(days=7)
    return await compute_metrics(session, period_start=start, period_end=end)


async def fetch_recent_samples_for_critique(
    session: AsyncSession,
    *,
    days: int = 7,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """self_critique용 샘플 — outcome 있는 approved 추천 우선."""
    end = date.today()
    start = end - timedelta(days=days)
    stmt = (
        select(AdvisorRecommendation)
        .where(AdvisorRecommendation.rec_date >= start)
        .where(AdvisorRecommendation.rec_date <= end)
        .order_by(AdvisorRecommendation.created_at.desc())
        .limit(limit * 2)  # outcome 없는 것도 있으니 buffer
    )
    recs = list((await session.execute(stmt)).scalars().all())
    if not recs:
        return []

    plan_ids = [r.trade_plan_id for r in recs if r.trade_plan_id]
    plans_by_id: dict[int, TradePlan] = {}
    if plan_ids:
        stmt_p = (
            select(TradePlan)
            .options(selectinload(TradePlan.outcomes))
            .where(TradePlan.id.in_(plan_ids))
        )
        for plan in (await session.execute(stmt_p)).scalars().all():
            plans_by_id[plan.id] = plan

    samples: list[dict[str, Any]] = []
    for rec in recs:
        outcome = None
        if rec.trade_plan_id and rec.trade_plan_id in plans_by_id:
            plan = plans_by_id[rec.trade_plan_id]
            for o in plan.outcomes:
                if o.horizon_days == 5:
                    outcome = o
                    break

        samples.append({
            "id": rec.id,
            "date": rec.rec_date.isoformat(),
            "rec_type": rec.rec_type,
            "symbol": rec.symbol,
            "confidence": float(rec.confidence) if rec.confidence else None,
            "entry": float(rec.entry_price) if rec.entry_price else None,
            "stop": float(rec.stop_price) if rec.stop_price else None,
            "target_1r": float(rec.target_1r) if rec.target_1r else None,
            "reasoning": (rec.reasoning_text or "")[:500],
            "status": rec.status,
            "outcome_5d_pct": float(outcome.pct_return) if outcome else None,
            "outcome_5d_alpha": float(outcome.alpha) if outcome else None,
            "outcome_win": bool(outcome.win_simple) if outcome else None,
            "hit_target_1r": bool(outcome.hit_target_1r) if outcome else None,
            "hit_stop": bool(outcome.hit_stop) if outcome else None,
        })

        if len(samples) >= limit:
            break

    return samples
