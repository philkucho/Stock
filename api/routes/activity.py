"""활동 기록 (Activity log) — 종합 거래/추천/체결 타임라인.

GET /api/activity/{date}
  → 그 날의 모든 이벤트를 시간순으로 통합:
    1. system_pick_logs (일일 picks 적재)
    2. advisor_recommendations (AI 추천 + status 변화)
    3. trade_plans (plan 생성/발송)
    4. pick_outcomes / trade_plan_outcomes (N일 후 실현 수익)
    5. Alpaca orders (실 broker 발송/체결/cancel)
    6. Alpaca positions (현재 보유 스냅샷)

Telegram raw 메시지 로그는 별도 DB 저장 안 함 — advisor_recommendations의
created_at = 알림 발송 시각, user_decision_at = Approve/Reject 시각으로 근사.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import (
    AdvisorRecommendation,
    PickOutcome,
    SystemPickLog,
    TradePlan,
    TradePlanOutcome,
)

router = APIRouter()


# ─────────── 스키마 ───────────


class ActivityEvent(BaseModel):
    ts: datetime  # UTC ISO
    type: str  # pick.logged / advisor.recommend / advisor.decided / plan.created / plan.sent / broker.filled / broker.canceled / broker.expired / outcome.recorded
    symbol: str | None = None
    summary: str  # 한 줄 요약
    details: dict[str, Any] = {}


class ActivitySummary(BaseModel):
    picks_count: int
    advisor_recommendations: int
    advisor_approved: int
    advisor_rejected: int
    advisor_expired: int
    plans_sent: int
    broker_orders: int
    broker_filled: int
    broker_canceled: int
    realized_pnl_usd: float


class ActivityResponse(BaseModel):
    date: date
    events: list[ActivityEvent]
    summary: ActivitySummary
    symbols: list[str]  # 그 날 등장한 unique symbols (필터용)


# ─────────── 라우트 ───────────


@router.get("/{target_date}", response_model=ActivityResponse)
async def activity(
    target_date: date,
    symbol: str | None = Query(default=None, description="종목 필터 (대문자, 빈값=전체)"),
    session: AsyncSession = Depends(get_session),
) -> ActivityResponse:
    """그 날의 모든 활동 이벤트를 시간순으로 반환."""
    events: list[ActivityEvent] = []
    summary = {
        "picks_count": 0,
        "advisor_recommendations": 0,
        "advisor_approved": 0,
        "advisor_rejected": 0,
        "advisor_expired": 0,
        "plans_sent": 0,
        "broker_orders": 0,
        "broker_filled": 0,
        "broker_canceled": 0,
        "realized_pnl_usd": 0.0,
    }
    symbols_seen: set[str] = set()

    sym_upper = symbol.upper() if symbol else None

    def _match(s: str | None) -> bool:
        return sym_upper is None or (s and s.upper() == sym_upper)

    # 1) system_pick_logs
    pick_logs_stmt = select(SystemPickLog).where(SystemPickLog.pick_date == target_date)
    pick_logs = list((await session.execute(pick_logs_stmt)).scalars().all())
    for pl in pick_logs:
        if not _match(pl.symbol):
            continue
        symbols_seen.add(pl.symbol.upper())
        summary["picks_count"] += 1
        events.append(
            ActivityEvent(
                ts=_floor_to_dt(pl.created_at, target_date),
                type="pick.logged",
                symbol=pl.symbol.upper(),
                summary=f"[{pl.system_id}] rank#{pl.rank} {pl.symbol} score={float(pl.score):.1f}",
                details={
                    "system_id": pl.system_id,
                    "rank": pl.rank,
                    "score": float(pl.score),
                    "sector": pl.sector,
                    "strategy_tag": pl.strategy_tag,
                    "entry_price": float(pl.entry_price) if pl.entry_price else None,
                },
            )
        )

    # 2) advisor_recommendations
    rec_stmt = select(AdvisorRecommendation).where(AdvisorRecommendation.rec_date == target_date)
    recs = list((await session.execute(rec_stmt)).scalars().all())
    for r in recs:
        if not _match(r.symbol):
            continue
        symbols_seen.add(r.symbol.upper())
        summary["advisor_recommendations"] += 1

        action_label = r.rec_type.replace("intraday_", "").replace("morning_brief", "morning")
        events.append(
            ActivityEvent(
                ts=r.created_at,
                type="advisor.recommend",
                symbol=r.symbol.upper(),
                summary=(
                    f"🤖 advisor {action_label}: {r.symbol} "
                    f"conf={float(r.confidence):.2f}"
                    + (f" entry=${float(r.entry_price):.2f}" if r.entry_price else "")
                    + f" (rec#{r.id})"
                ),
                details={
                    "rec_id": r.id,
                    "rec_type": r.rec_type,
                    "confidence": float(r.confidence) if r.confidence else None,
                    "entry": float(r.entry_price) if r.entry_price else None,
                    "stop": float(r.stop_price) if r.stop_price else None,
                    "target_1r": float(r.target_1r) if r.target_1r else None,
                    "target_2r": float(r.target_2r) if r.target_2r else None,
                    "qty": r.qty,
                    "reasoning": (r.reasoning_text or "")[:500],
                    "model_version": r.model_version,
                    "telegram_message_id": r.telegram_message_id,
                },
            )
        )

        # status 변화 이벤트 (approved/rejected/expired)
        if r.status in ("approved", "rejected", "expired", "executed") and r.user_decision_at:
            if r.status == "approved":
                summary["advisor_approved"] += 1
            elif r.status == "rejected":
                summary["advisor_rejected"] += 1
            elif r.status == "expired":
                summary["advisor_expired"] += 1
            events.append(
                ActivityEvent(
                    ts=r.user_decision_at,
                    type="advisor.decided",
                    symbol=r.symbol.upper(),
                    summary=(
                        f"{'✅' if r.status == 'approved' else '❌' if r.status == 'rejected' else '⌛'} "
                        f"{r.symbol} {action_label} → {r.status} (rec#{r.id})"
                        + (f" — {r.reject_reason[:80]}" if r.reject_reason else "")
                    ),
                    details={
                        "rec_id": r.id,
                        "status": r.status,
                        "reject_reason": r.reject_reason,
                        "trade_plan_id": r.trade_plan_id,
                    },
                )
            )
        elif r.status == "expired" and not r.user_decision_at:
            # expires_at 경과로 자동 만료된 케이스
            summary["advisor_expired"] += 1
            events.append(
                ActivityEvent(
                    ts=r.expires_at,
                    type="advisor.decided",
                    symbol=r.symbol.upper(),
                    summary=f"⌛ {r.symbol} {action_label} → expired (TTL 경과, rec#{r.id})",
                    details={"rec_id": r.id, "status": "expired"},
                )
            )

    # 3) trade_plans
    plan_stmt = select(TradePlan).where(TradePlan.plan_date == target_date)
    plans = list((await session.execute(plan_stmt)).scalars().all())
    plan_by_id: dict[int, TradePlan] = {p.id: p for p in plans}
    for p in plans:
        if not _match(p.symbol):
            continue
        symbols_seen.add(p.symbol.upper())
        events.append(
            ActivityEvent(
                ts=p.created_at,
                type="plan.created",
                symbol=p.symbol.upper(),
                summary=(
                    f"📋 plan [{p.dispatch_mode}] {p.symbol} qty={p.shares} "
                    f"entry=${float(p.entry_price):.2f} stop=${float(p.stop_price):.2f} "
                    f"t1=${float(p.target_1r):.2f}"
                ),
                details={
                    "plan_id": p.id,
                    "dispatch_mode": p.dispatch_mode,
                    "confirm_status": p.confirm_status,
                    "shares": p.shares,
                    "amount_usd": float(p.amount_usd) if p.amount_usd else None,
                    "entry": float(p.entry_price),
                    "stop": float(p.stop_price),
                    "target_1r": float(p.target_1r),
                    "target_2r": float(p.target_2r),
                    "risk_usd": float(p.risk_usd) if p.risk_usd else None,
                    "sector": p.sector,
                    "broker_order_ids": p.broker_order_ids or [],
                },
            )
        )
        if p.confirm_status == "sent" and p.broker_order_ids:
            summary["plans_sent"] += 1

    # 4) outcomes (pick + trade_plan)
    if pick_logs:
        pick_ids = [pl.id for pl in pick_logs if _match(pl.symbol)]
        if pick_ids:
            po_stmt = select(PickOutcome).where(PickOutcome.pick_log_id.in_(pick_ids))
            for o in (await session.execute(po_stmt)).scalars().all():
                pl_match = next((pl for pl in pick_logs if pl.id == o.pick_log_id), None)
                sym = pl_match.symbol.upper() if pl_match else None
                summary["realized_pnl_usd"] += float(o.realized_pnl_usd)
                events.append(
                    ActivityEvent(
                        ts=datetime.combine(o.exit_date, time(16, 0), tzinfo=timezone.utc),
                        type="outcome.recorded",
                        symbol=sym,
                        summary=(
                            f"📊 {sym} {o.horizon_days}d 결과: "
                            f"{float(o.pct_return):+.2f}% "
                            f"(α {float(o.alpha):+.2f}% vs SPY) "
                            f"PnL ${float(o.realized_pnl_usd):+.2f}"
                        ),
                        details={
                            "pick_log_id": o.pick_log_id,
                            "horizon_days": o.horizon_days,
                            "exit_date": o.exit_date.isoformat(),
                            "exit_price": float(o.exit_price),
                            "pct_return": float(o.pct_return),
                            "alpha": float(o.alpha),
                            "realized_pnl_usd": float(o.realized_pnl_usd),
                        },
                    )
                )

    if plans:
        plan_ids = [p.id for p in plans if _match(p.symbol)]
        if plan_ids:
            tpo_stmt = select(TradePlanOutcome).where(TradePlanOutcome.trade_plan_id.in_(plan_ids))
            for o in (await session.execute(tpo_stmt)).scalars().all():
                plan_match = plan_by_id.get(o.trade_plan_id)
                sym = plan_match.symbol.upper() if plan_match else None
                events.append(
                    ActivityEvent(
                        ts=datetime.combine(o.exit_date, time(16, 0), tzinfo=timezone.utc),
                        type="outcome.recorded",
                        symbol=sym,
                        summary=(
                            f"📊 {sym} plan {o.horizon_days}d: "
                            f"{float(o.pct_return):+.2f}% "
                            f"realized ${float(o.realized_pnl_usd):+.2f}"
                        ),
                        details={
                            "trade_plan_id": o.trade_plan_id,
                            "horizon_days": o.horizon_days,
                            "exit_date": o.exit_date.isoformat(),
                            "exit_price": float(o.exit_price),
                            "pct_return": float(o.pct_return),
                            "realized_pnl_usd": float(o.realized_pnl_usd),
                            "hit_target_1r": o.hit_target_1r,
                            "hit_target_2r": o.hit_target_2r,
                            "hit_stop": o.hit_stop,
                        },
                    )
                )

    # 5) Alpaca orders — 해당 날짜에 submitted/filled/canceled
    try:
        from broker_adapter import get_adapter

        adapter = get_adapter()
        try:
            all_orders = await adapter.get_orders(status="all")
        finally:
            await adapter.close()
    except Exception as exc:
        # broker 조회 실패해도 DB 이벤트만으로도 의미 있음
        all_orders = []
        events.append(
            ActivityEvent(
                ts=datetime.now(timezone.utc),
                type="system.warning",
                summary=f"⚠️ broker 조회 실패: {exc.__class__.__name__}",
                details={"error": str(exc)},
            )
        )

    target_utc_start = datetime.combine(target_date, time(0, 0), tzinfo=timezone.utc)
    target_utc_end = datetime.combine(target_date, time(23, 59, 59), tzinfo=timezone.utc)

    for o in all_orders:
        sub_at = getattr(o, "submitted_at", None)
        if not sub_at:
            continue
        if sub_at.tzinfo is None:
            sub_at = sub_at.replace(tzinfo=timezone.utc)
        # 그 날 submitted 또는 filled/canceled
        if not (target_utc_start <= sub_at <= target_utc_end):
            continue
        if not _match(o.symbol):
            continue
        symbols_seen.add(o.symbol.upper())
        summary["broker_orders"] += 1
        events.append(
            ActivityEvent(
                ts=sub_at,
                type="broker.submitted",
                symbol=o.symbol.upper(),
                summary=(
                    f"📤 broker submit: {o.symbol} {o.side} {o.qty} {o.order_type} "
                    f"→ {o.status}"
                ),
                details={
                    "order_id": o.order_id,
                    "side": o.side,
                    "qty": o.qty,
                    "order_type": o.order_type,
                    "status": o.status,
                    "limit_price": getattr(o, "limit_price", None),
                    "stop_price": getattr(o, "stop_price", None),
                    "client_order_id": getattr(o, "client_order_id", None),
                },
            )
        )

        # status 종착 이벤트 (filled / canceled / expired)
        if o.status in ("filled", "canceled", "expired"):
            filled_at = (getattr(o, "filled_at", None) or sub_at)
            if filled_at and filled_at.tzinfo is None:
                filled_at = filled_at.replace(tzinfo=timezone.utc)
            if o.status == "filled":
                summary["broker_filled"] += 1
                fill_px = getattr(o, "filled_avg_price", None) or "?"
                events.append(
                    ActivityEvent(
                        ts=filled_at,
                        type="broker.filled",
                        symbol=o.symbol.upper(),
                        summary=f"💰 {o.symbol} {o.side} {o.qty} filled @ ${fill_px}",
                        details={
                            "order_id": o.order_id,
                            "side": o.side,
                            "qty": o.qty,
                            "filled_qty": getattr(o, "filled_qty", o.qty),
                            "filled_avg_price": fill_px,
                        },
                    )
                )
            elif o.status == "canceled":
                summary["broker_canceled"] += 1
                events.append(
                    ActivityEvent(
                        ts=filled_at,
                        type="broker.canceled",
                        symbol=o.symbol.upper(),
                        summary=f"❌ {o.symbol} {o.side} {o.qty} canceled",
                        details={"order_id": o.order_id, "side": o.side, "qty": o.qty},
                    )
                )
            elif o.status == "expired":
                events.append(
                    ActivityEvent(
                        ts=filled_at,
                        type="broker.expired",
                        symbol=o.symbol.upper(),
                        summary=f"⌛ {o.symbol} {o.side} {o.qty} {o.order_type} expired",
                        details={"order_id": o.order_id, "side": o.side, "qty": o.qty,
                                 "order_type": o.order_type},
                    )
                )

    # 시간순 정렬 (최근→과거, 내림차순) — 사용자는 최신 이벤트를 위에서 먼저 보고 싶어함
    events.sort(key=lambda e: e.ts, reverse=True)

    summary["realized_pnl_usd"] = round(summary["realized_pnl_usd"], 2)

    return ActivityResponse(
        date=target_date,
        events=events,
        summary=ActivitySummary(**summary),
        symbols=sorted(symbols_seen),
    )


def _floor_to_dt(dt: datetime | None, fallback_date: date) -> datetime:
    """created_at이 없으면 그 날 00:00 UTC fallback (Pydantic UTC tz 일관성)."""
    if dt is None:
        return datetime.combine(fallback_date, time(0, 0), tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
