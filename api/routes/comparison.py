"""3 시스템(v3 / scanner / integrated) picks 비교 엔드포인트.

GET  /api/comparison/today              — 오늘 3 시스템 picks (선정 직후 비교)
GET  /api/comparison/picks/{date}       — 특정 일자 picks + outcome
GET  /api/comparison/summary            — 시스템별 누적 통계 (cumulative PnL, win rate, Sharpe)
POST /api/comparison/log-today          — 수동 picks 로그 트리거
POST /api/comparison/backfill-outcomes  — 수동 outcome 백필 트리거
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db import get_session
from api.db.models import PickOutcome, SystemPickLog
from scanner.comparison import (
    EFFECTIVE_SYSTEMS,
    HOLDING_HORIZONS,
    SIM_CAPITAL_PER_SYSTEM,
    SYSTEMS,
    TOP_N,
    effective_system_id,
)

router = APIRouter()


# ─────────── 응답 스키마 ───────────


class OutcomeOut(BaseModel):
    horizon_days: int
    exit_date: date
    exit_price: Decimal
    pct_return: Decimal
    spy_pct_return: Decimal
    alpha: Decimal
    win_simple: bool
    win_alpha: bool
    realized_pnl_usd: Decimal


class PickLogOut(BaseModel):
    id: int
    system_id: str
    pick_date: date
    rank: int
    symbol: str
    score: Decimal
    sector: str | None
    strategy_tag: str
    entry_price: Decimal | None
    sim_capital_usd: Decimal
    score_meta: dict[str, Any]
    outcomes: list[OutcomeOut]


class TodayResponse(BaseModel):
    pick_date: date
    by_system: dict[str, list[PickLogOut]]


class SystemKPI(BaseModel):
    system_id: str
    n_picks: int  # 총 픽 개수
    n_with_outcome: int  # outcome 있는 픽
    horizon_kpis: dict[int, dict[str, float]]  # {1: {avg_return, win_rate, ...}, 5: ...}


class SummaryResponse(BaseModel):
    period_start: date
    period_end: date
    systems: list[SystemKPI]
    cumulative_pnl_curve: dict[str, list[dict[str, Any]]]  # {system_id: [{date, cum_pnl_5d}]}


# ─────────── 조회 ───────────


@router.get("/today", response_model=TodayResponse)
async def today(
    target_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> TodayResponse:
    target = target_date or date.today()
    return await _picks_for(session, target)


@router.get("/picks/{pick_date}", response_model=TodayResponse)
async def picks_for_date(
    pick_date: date,
    session: AsyncSession = Depends(get_session),
) -> TodayResponse:
    return await _picks_for(session, pick_date)


async def _picks_for(session: AsyncSession, pick_date: date) -> TodayResponse:
    stmt = (
        select(SystemPickLog)
        .options(selectinload(SystemPickLog.outcomes))
        .where(SystemPickLog.pick_date == pick_date)
        .order_by(SystemPickLog.system_id, SystemPickLog.rank)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    # 4-way 분리: integrated는 score_meta.source 기준 v10 / v9_fallback 두 버킷으로
    by_system: dict[str, list[PickLogOut]] = {s: [] for s in EFFECTIVE_SYSTEMS}
    for r in rows:
        outcomes = [
            OutcomeOut(
                horizon_days=o.horizon_days,
                exit_date=o.exit_date,
                exit_price=o.exit_price,
                pct_return=o.pct_return,
                spy_pct_return=o.spy_pct_return,
                alpha=o.alpha,
                win_simple=o.win_simple,
                win_alpha=o.win_alpha,
                realized_pnl_usd=o.realized_pnl_usd,
            )
            for o in sorted(r.outcomes, key=lambda x: x.horizon_days)
        ]
        eff_sys = effective_system_id(r.system_id, r.score_meta)
        by_system.setdefault(eff_sys, []).append(
            PickLogOut(
                id=r.id,
                system_id=eff_sys,
                pick_date=r.pick_date,
                rank=r.rank,
                symbol=r.symbol,
                score=r.score,
                sector=r.sector,
                strategy_tag=r.strategy_tag,
                entry_price=r.entry_price,
                sim_capital_usd=r.sim_capital_usd,
                score_meta=r.score_meta or {},
                outcomes=outcomes,
            )
        )
    return TodayResponse(pick_date=pick_date, by_system=by_system)


# ─────────── 누적 통계 ───────────


def _kpis_for(picks_with_outcomes: list[tuple], horizon: int) -> dict[str, float]:
    """horizon별 KPI: avg_return, avg_alpha, win_rate, win_alpha_rate, Sharpe (annualized).

    picks_with_outcomes: list of (pick_log, outcome at this horizon)
    """
    rets = [float(o.pct_return) for _, o in picks_with_outcomes if o is not None]
    alphas = [float(o.alpha) for _, o in picks_with_outcomes if o is not None]
    wins = [bool(o.win_simple) for _, o in picks_with_outcomes if o is not None]
    win_alphas = [bool(o.win_alpha) for _, o in picks_with_outcomes if o is not None]
    pnls = [float(o.realized_pnl_usd) for _, o in picks_with_outcomes if o is not None]

    n = len(rets)
    if n == 0:
        return {
            "n": 0,
            "avg_return_pct": 0.0,
            "avg_alpha_pct": 0.0,
            "win_rate": 0.0,
            "win_alpha_rate": 0.0,
            "sharpe": 0.0,
            "total_pnl_usd": 0.0,
            "max_return_pct": 0.0,
            "min_return_pct": 0.0,
        }

    avg_ret = sum(rets) / n
    avg_alpha = sum(alphas) / n
    win_rate = sum(wins) / n
    win_alpha_rate = sum(win_alphas) / n

    # Sharpe (return / std × sqrt(252/horizon)) — 단순 정규화
    if n >= 2:
        mean = avg_ret
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        std = var ** 0.5
        if std > 0:
            sharpe = (mean / std) * math.sqrt(252.0 / horizon)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    return {
        "n": n,
        "avg_return_pct": round(avg_ret, 4),
        "avg_alpha_pct": round(avg_alpha, 4),
        "win_rate": round(win_rate, 4),
        "win_alpha_rate": round(win_alpha_rate, 4),
        "sharpe": round(sharpe, 4),
        "total_pnl_usd": round(sum(pnls), 2),
        "max_return_pct": round(max(rets), 4),
        "min_return_pct": round(min(rets), 4),
    }


@router.get("/summary", response_model=SummaryResponse)
async def summary(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> SummaryResponse:
    """최근 N일 시스템별 누적 통계 + cumulative PnL 곡선."""
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    # picks + outcomes
    stmt = (
        select(SystemPickLog)
        .options(selectinload(SystemPickLog.outcomes))
        .where(SystemPickLog.pick_date >= start_date)
        .where(SystemPickLog.pick_date <= end_date)
    )
    pick_logs = list((await session.execute(stmt)).scalars().all())

    # 시스템별 그룹 — integrated는 v10 / v9_fallback 두 버킷으로 분해
    by_system: dict[str, list] = {s: [] for s in EFFECTIVE_SYSTEMS}
    for pl in pick_logs:
        eff = effective_system_id(pl.system_id, pl.score_meta)
        by_system.setdefault(eff, []).append(pl)

    # 시스템별 KPI
    kpi_list: list[SystemKPI] = []
    for sys_id, logs in by_system.items():
        horizon_kpis: dict[int, dict[str, float]] = {}
        for h in HOLDING_HORIZONS:
            pairs = []
            for pl in logs:
                outcome = next(
                    (o for o in pl.outcomes if o.horizon_days == h), None
                )
                pairs.append((pl, outcome))
            horizon_kpis[h] = _kpis_for(pairs, h)
        n_with_outcome = sum(
            1 for pl in logs if any(o for o in pl.outcomes)
        )
        kpi_list.append(
            SystemKPI(
                system_id=sys_id,
                n_picks=len(logs),
                n_with_outcome=n_with_outcome,
                horizon_kpis=horizon_kpis,
            )
        )

    # Cumulative PnL 곡선 (5d horizon 기준) — 4 effective system
    pnl_curve: dict[str, list[dict[str, Any]]] = {s: [] for s in EFFECTIVE_SYSTEMS}
    for sys_id, logs in by_system.items():
        # date별 5d outcome 합계
        daily_pnl: dict[date, float] = defaultdict(float)
        for pl in logs:
            o5 = next((o for o in pl.outcomes if o.horizon_days == 5), None)
            if o5:
                daily_pnl[pl.pick_date] += float(o5.realized_pnl_usd)
        # 누적
        cum = 0.0
        for d in sorted(daily_pnl.keys()):
            cum += daily_pnl[d]
            pnl_curve.setdefault(sys_id, []).append(
                {"date": d.isoformat(), "cum_pnl_usd": round(cum, 2)}
            )

    return SummaryResponse(
        period_start=start_date,
        period_end=end_date,
        systems=kpi_list,
        cumulative_pnl_curve=pnl_curve,
    )


# ─────────── 수동 트리거 ───────────


@router.post("/log-today")
async def trigger_log_today(
    target_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """매일 picks 로그 — 09:30 ET cron이 호출. 수동도 가능."""
    from scanner.comparison.logger import log_daily_picks

    try:
        result = await log_daily_picks(session, target_date)
        return {"status": "ok", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backfill-outcomes")
async def trigger_backfill(
    target_date: date | None = Query(default=None),
    lookback_days: int = Query(default=30, ge=1, le=180),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """1d/5d/10d outcome 백필 — 16:30 ET cron이 호출."""
    from scanner.comparison.outcomes import backfill_pick_outcomes

    try:
        result = await backfill_pick_outcomes(session, target_date, lookback_days=lookback_days)
        return {"status": "ok", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
