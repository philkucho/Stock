"""AI 자문 에이전트 전체 흐름.

run_morning_brief(target):
  1. build_morning_context — picks/regime/positions/news 수집
  2. ClaudeAdvisorClient.call('morning_brief') — Claude Opus 호출
  3. parse_claude_json → MorningBriefResponse
  4. 각 Recommendation을 현재가 ±5% 검증 후 DB upsert
  5. Telegram으로 inline approve/reject keyboard 전송

run_intraday_check(symbol, target, trigger_reason):
  1. build_intraday_context
  2. Claude call('intraday_check')
  3. parse IntradayCheckResponse
  4. confidence >= MIN_CONFIDENCE면 DB upsert + Telegram
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AdvisorRecommendation
from services.advisor.context_builder import (
    build_intraday_context,
    build_morning_context,
    fetch_current_price,
)
from services.advisor.llm import get_advisor_client
from services.advisor.recommendation import (
    IntradayCheckResponse,
    MorningBriefResponse,
    Recommendation,
    RecommendationValidationError,
    parse_claude_json,
)

logger = logging.getLogger("advisor.service")


def _ttl_seconds() -> int:
    return int(os.environ.get("ADVISOR_APPROVAL_TTL_SEC", "300"))


def _min_intraday_confidence() -> float:
    return float(os.environ.get("ADVISOR_INTRADAY_MIN_CONFIDENCE", "0.6"))


def _advisor_enabled() -> bool:
    return os.environ.get("ADVISOR_ENABLED", "false").strip().lower() == "true"


async def run_morning_brief(
    session: AsyncSession,
    target: date,
    *,
    notify_telegram: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """장 시작 전 자문 1회 실행. preopen cron이 호출.

    멱등성: 같은 (rec_date, symbol, 'morning')은 ON CONFLICT DO NOTHING.
    재실행해도 새 추천 생성 안 함 (이미 사용자가 결정 중일 수 있음).
    """
    out: dict[str, Any] = {
        "date": target.isoformat(),
        "rec_type": "morning",
        "advisor_enabled": _advisor_enabled(),
        "dry_run": dry_run,
        "created": [],
        "skipped": [],
    }

    if not _advisor_enabled() and not dry_run:
        out["status"] = "disabled"
        out["reason"] = "ADVISOR_ENABLED=false — dry-run로 호출하려면 dry_run=True"
        return out

    # 1) Context
    try:
        ctx = await build_morning_context(session, target)
    except Exception as exc:
        logger.exception("[advisor.morning] context build failed")
        out["status"] = "context_error"
        out["error"] = str(exc)
        return out

    out["context"] = {
        "picks_count": len(ctx.get("picks", [])),
        "positions_count": len(ctx.get("positions", [])),
        "regime_mode": ctx.get("regime", {}).get("mode"),
    }

    # 2) LLM 호출 (provider는 ADVISOR_PROVIDER 환경변수로 결정 — google | anthropic)
    client = get_advisor_client()
    try:
        text, usage = await client.call(
            prompt_name="morning_brief",
            user_payload=ctx,
            max_tokens=2500,
            temperature=0.3,
        )
    except Exception as exc:
        logger.exception("[advisor.morning] claude call failed")
        out["status"] = "claude_error"
        out["error"] = str(exc)
        return out
    out["usage"] = usage

    # 3) Parse
    try:
        parsed = parse_claude_json(text, MorningBriefResponse)
        assert isinstance(parsed, MorningBriefResponse)
    except RecommendationValidationError as exc:
        logger.warning("[advisor.morning] parse failed: %s", exc)
        out["status"] = "parse_error"
        out["error"] = str(exc)
        out["raw_response"] = text[:1000]
        return out

    out["market_summary"] = parsed.market_summary
    out["risks_to_watch"] = parsed.risks_to_watch

    # 4) Persist + Telegram
    for rec in parsed.recommendations:
        try:
            current = fetch_current_price(rec.symbol)
            if current is not None:
                rec.check_against_current_price(current)
        except RecommendationValidationError as exc:
            logger.warning("[advisor.morning] %s skipped: %s", rec.symbol, exc)
            out["skipped"].append({
                "symbol": rec.symbol,
                "reason": f"price_check_fail: {exc}",
            })
            continue

        try:
            rec_id = await _upsert_recommendation(
                session,
                target=target,
                rec_type="morning",
                rec=rec,
                context_snapshot=_compact_snapshot(ctx, rec.symbol),
                model_version=usage.get("model"),
                prompt_version=usage.get("prompt_version"),
            )
        except Exception as exc:
            logger.exception("[advisor.morning] persist %s failed", rec.symbol)
            out["skipped"].append({"symbol": rec.symbol, "reason": f"db: {exc}"})
            continue

        if rec_id is None:
            out["skipped"].append({
                "symbol": rec.symbol,
                "reason": "duplicate (already pending today)",
            })
            continue

        out["created"].append({
            "id": rec_id,
            "symbol": rec.symbol,
            "confidence": float(rec.confidence),
            "entry": float(rec.entry),
        })

        if notify_telegram and not dry_run:
            try:
                from services.advisor.notifications.telegram import send_recommendation_notification

                await send_recommendation_notification(session, rec_id)
            except Exception as exc:
                logger.warning("[advisor.morning] telegram notify failed: %s", exc)

    await session.commit()
    out["status"] = "ok"
    out["created_count"] = len(out["created"])
    return out


async def run_intraday_check(
    session: AsyncSession,
    symbol: str,
    target: date,
    trigger_reason: str,
    *,
    notify_telegram: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """장중 단일 종목 자문. intraday_monitor가 호출.

    confidence < ADVISOR_INTRADAY_MIN_CONFIDENCE이면 DB만 로깅하고 Telegram 안 보냄.
    """
    out: dict[str, Any] = {
        "symbol": symbol.upper(),
        "trigger_reason": trigger_reason,
        "advisor_enabled": _advisor_enabled(),
        "dry_run": dry_run,
    }
    if not _advisor_enabled() and not dry_run:
        out["status"] = "disabled"
        return out

    try:
        ctx = await build_intraday_context(session, symbol, target, trigger_reason)
    except Exception as exc:
        logger.exception("[advisor.intraday] context build failed")
        out["status"] = "context_error"
        out["error"] = str(exc)
        return out

    client = ClaudeAdvisorClient()
    try:
        text, usage = await client.call(
            prompt_name="intraday_check",
            user_payload=ctx,
            max_tokens=1200,
            temperature=0.2,
        )
    except Exception as exc:
        logger.exception("[advisor.intraday] claude call failed")
        out["status"] = "claude_error"
        out["error"] = str(exc)
        return out
    out["usage"] = usage

    try:
        parsed = parse_claude_json(text, IntradayCheckResponse)
        assert isinstance(parsed, IntradayCheckResponse)
    except RecommendationValidationError as exc:
        out["status"] = "parse_error"
        out["error"] = str(exc)
        out["raw_response"] = text[:1000]
        return out

    rec = parsed.decision
    out["context_note"] = parsed.context_note
    out["action"] = rec.action.value
    out["confidence"] = float(rec.confidence)

    min_conf = _min_intraday_confidence()
    if float(rec.confidence) < min_conf:
        out["status"] = "below_threshold"
        out["reason"] = f"confidence {float(rec.confidence):.2f} < {min_conf}"
        # DB에는 저장 (학습 데이터). Telegram 알림은 안 보냄.
        notify_telegram = False

    rec_type_map = {
        "enter": "intraday_entry",
        "add": "intraday_add",
        "trim": "intraday_exit",
        "exit": "intraday_exit",
        "hold": "intraday_hold",
    }
    rec_type = rec_type_map.get(rec.action.value, "intraday_hold")

    # hold는 DB 기록만, recommendation 저장 X (액션 없음)
    if rec.action.value == "hold":
        out["status"] = "hold"
        return out

    try:
        rec_id = await _upsert_recommendation(
            session,
            target=target,
            rec_type=rec_type,
            rec=rec,
            context_snapshot=_compact_intraday_snapshot(ctx),
            model_version=usage.get("model"),
            prompt_version=usage.get("prompt_version"),
        )
    except Exception as exc:
        logger.exception("[advisor.intraday] persist failed")
        out["status"] = "db_error"
        out["error"] = str(exc)
        return out

    if rec_id is None:
        out["status"] = "duplicate"
        return out

    out["recommendation_id"] = rec_id

    if notify_telegram:
        try:
            from services.advisor.notifications.telegram import send_recommendation_notification

            await send_recommendation_notification(session, rec_id)
        except Exception as exc:
            logger.warning("[advisor.intraday] telegram notify failed: %s", exc)

    await session.commit()
    out["status"] = "ok"
    return out


# ──── Internals ────


async def _upsert_recommendation(
    session: AsyncSession,
    *,
    target: date,
    rec_type: str,
    rec: Recommendation,
    context_snapshot: dict[str, Any],
    model_version: str | None,
    prompt_version: str | None,
) -> int | None:
    """DB upsert. 이미 (date, symbol, rec_type) pending이 있으면 None 반환 (skip)."""
    ttl_sec = _ttl_seconds()
    expires_at = Recommendation.default_expires_at(ttl_sec)

    row = {
        "rec_date": target,
        "rec_type": rec_type,
        "symbol": rec.symbol.upper(),
        "side": rec.side,
        "entry_price": Decimal(f"{float(rec.entry):.4f}") if rec.entry else None,
        "stop_price": Decimal(f"{float(rec.stop):.4f}") if rec.stop else None,
        "target_1r": Decimal(f"{float(rec.target_1r):.4f}") if rec.target_1r else None,
        "target_2r": Decimal(f"{float(rec.target_2r):.4f}") if rec.target_2r else None,
        "qty": rec.qty,
        "confidence": Decimal(f"{float(rec.confidence):.3f}"),
        "reasoning_text": rec.reasoning,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "context_snapshot": context_snapshot,
        "status": "pending",
        "expires_at": expires_at,
    }

    stmt = pg_insert(AdvisorRecommendation).values(row)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_advisor_rec_date_symbol_type")
    result = await session.execute(stmt)

    # ON CONFLICT DO NOTHING의 returning row를 쓰면 None. 별도 select.
    if result.rowcount == 0:
        return None

    fetch = await session.execute(
        select(AdvisorRecommendation.id)
        .where(AdvisorRecommendation.rec_date == target)
        .where(AdvisorRecommendation.symbol == rec.symbol.upper())
        .where(AdvisorRecommendation.rec_type == rec_type)
    )
    return fetch.scalar()


def _compact_snapshot(ctx: dict[str, Any], symbol: str) -> dict[str, Any]:
    """morning context에서 해당 심볼+regime+account만 추려서 snapshot 저장.

    DB에 전체 ctx를 박으면 너무 큼. 사후 디버깅에 필요한 부분만.
    """
    pick = next(
        (p for p in ctx.get("picks", []) if p.get("symbol") == symbol), None
    )
    return {
        "as_of": ctx.get("as_of"),
        "regime": ctx.get("regime", {}),
        "account": ctx.get("account", {}),
        "positions_count": len(ctx.get("positions", [])),
        "pick": pick,
        "recent_outcomes": ctx.get("recent_outcomes", {}),
    }


def _compact_intraday_snapshot(ctx: dict[str, Any]) -> dict[str, Any]:
    bars = ctx.get("intraday_bars_1m", [])
    return {
        "as_of": ctx.get("as_of"),
        "trigger_reason": ctx.get("trigger_reason"),
        "trade_plan": ctx.get("trade_plan"),
        "position": ctx.get("position"),
        "regime": ctx.get("regime"),
        "last_bar": bars[-1] if bars else None,
        "news_count": len(ctx.get("news_recent", [])),
    }


# ──── Approval / rejection ────


async def expire_overdue_recommendations(session: AsyncSession) -> int:
    """status='pending' AND expires_at < now() → 'expired'.

    daily_pipeline trade phase 직전, advisor 라우트 호출 직후 등에 호출.
    반환: 만료 처리된 row 수.
    """
    from sqlalchemy import update

    now = datetime.now(timezone.utc)
    stmt = (
        update(AdvisorRecommendation)
        .where(AdvisorRecommendation.status == "pending")
        .where(AdvisorRecommendation.expires_at < now)
        .values(status="expired")
    )
    result = await session.execute(stmt)
    if result.rowcount:
        await session.commit()
        logger.info("[advisor.expire] %d recommendations expired", result.rowcount)
    return result.rowcount or 0


async def approve_recommendation(
    session: AsyncSession,
    rec_id: int,
    *,
    user_amount_usd: float | None = None,
) -> dict[str, Any]:
    """사용자 승인 처리.

    morning 추천 → trade_plan에 user_fixed로 upsert. 09:30 cron이 발송.
    intraday_entry / intraday_add → trade_plan upsert (cron 또는 즉시 발송).
    intraday_exit → ⚠️ 직접 broker.close_position 호출 (Phase 2에서 구현).

    user_amount_usd 미입력 시 equity / 5 사용 (broker.get_account에서 산출).
    """
    rec = await session.get(AdvisorRecommendation, rec_id)
    if rec is None:
        raise ValueError(f"recommendation #{rec_id} not found")
    if rec.status != "pending":
        return {"status": rec.status, "message": f"이미 {rec.status} 상태 — 변경 안 함"}
    if rec.expires_at < datetime.now(timezone.utc):
        rec.status = "expired"
        await session.commit()
        return {"status": "expired", "message": "TTL 경과 — 자동 만료"}

    # intraday_exit는 별도 처리 (Phase 2)
    if rec.rec_type == "intraday_exit":
        rec.status = "approved"
        rec.user_decision_at = datetime.now(timezone.utc)
        await session.commit()
        return {
            "status": "approved",
            "rec_type": rec.rec_type,
            "message": "intraday_exit approved — 장중 monitor에서 청산 처리 (Phase 2)",
        }

    # morning / intraday_entry / intraday_add → trade_plan upsert
    from api.db.models import TradePlan

    if not (rec.entry_price and rec.stop_price and rec.target_1r and rec.target_2r):
        raise ValueError(f"recommendation #{rec_id} missing price levels")

    # 금액 산정
    amount_usd = user_amount_usd
    if amount_usd is None:
        try:
            from broker_adapter import get_adapter

            adapter = get_adapter()
            try:
                acc = await adapter.get_account()
                amount_usd = acc.equity / 5.0
            finally:
                await adapter.close()
        except Exception:
            amount_usd = 2000.0  # fallback

    entry = float(rec.entry_price)
    shares = max(1, int(amount_usd / entry))
    risk_per_share = entry - float(rec.stop_price)
    risk_usd = shares * risk_per_share

    row = {
        "plan_date": rec.rec_date,
        "symbol": rec.symbol,
        "rank": 1,
        "amount_usd": Decimal(f"{(shares * entry):.2f}"),
        "entry_price": rec.entry_price,
        "stop_price": rec.stop_price,
        "target_1r": rec.target_1r,
        "target_2r": rec.target_2r,
        "composite_score": Decimal(f"{float(rec.confidence) * 100:.2f}"),
        "score_meta": {
            "source": "advisor",
            "advisor_rec_id": rec.id,
            "model_version": rec.model_version,
            "prompt_version": rec.prompt_version,
            "confidence": float(rec.confidence),
        },
        "shares": shares,
        "risk_usd": Decimal(f"{risk_usd:.2f}"),
        "dispatch_mode": "user_fixed",
    }
    stmt = pg_insert(TradePlan).values(row)
    update_cols = {
        "rank": stmt.excluded.rank,
        "amount_usd": stmt.excluded.amount_usd,
        "entry_price": stmt.excluded.entry_price,
        "stop_price": stmt.excluded.stop_price,
        "target_1r": stmt.excluded.target_1r,
        "target_2r": stmt.excluded.target_2r,
        "composite_score": stmt.excluded.composite_score,
        "score_meta": stmt.excluded.score_meta,
        "shares": stmt.excluded.shares,
        "risk_usd": stmt.excluded.risk_usd,
        "dispatch_mode": stmt.excluded.dispatch_mode,
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_trade_plan_date_sym", set_=update_cols
    )
    await session.execute(stmt)

    # trade_plan id fetch
    fetch = await session.execute(
        select(TradePlan.id)
        .where(TradePlan.plan_date == rec.rec_date)
        .where(TradePlan.symbol == rec.symbol)
    )
    plan_id = fetch.scalar()

    rec.status = "approved"
    rec.user_decision_at = datetime.now(timezone.utc)
    rec.trade_plan_id = plan_id
    await session.commit()

    return {
        "status": "approved",
        "rec_type": rec.rec_type,
        "trade_plan_id": plan_id,
        "shares": shares,
        "amount_usd": round(shares * entry, 2),
    }


async def reject_recommendation(
    session: AsyncSession, rec_id: int, reason: str
) -> dict[str, Any]:
    rec = await session.get(AdvisorRecommendation, rec_id)
    if rec is None:
        raise ValueError(f"recommendation #{rec_id} not found")
    if rec.status != "pending":
        return {"status": rec.status, "message": f"이미 {rec.status} 상태 — 변경 안 함"}
    rec.status = "rejected"
    rec.reject_reason = (reason or "").strip()[:1000]
    rec.user_decision_at = datetime.now(timezone.utc)
    await session.commit()
    return {"status": "rejected", "rec_type": rec.rec_type}
