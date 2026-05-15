"""Telegram webhook 수신 — callback_query (Approve/Reject 버튼) 처리.

setWebhook 시 secret_token 등록 → 수신 시 X-Telegram-Bot-Api-Secret-Token 헤더로 검증.

흐름:
  1. 사용자가 Approve/Reject 버튼 클릭
  2. Telegram → POST /api/telegram/webhook
  3. callback_data 파싱 ("adv:ap:123")
  4. approve_recommendation / reject_recommendation 호출
  5. 원본 메시지에 결과 badge 편집 (중복 클릭 방지)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import AdvisorRecommendation
from services.advisor.notifications.telegram import (
    ACTION_APPROVE,
    ACTION_REJECT,
    ACTION_VIEW,
    parse_callback_data,
    send_decision_followup,
    validate_secret,
)
from services.advisor.service import (
    approve_recommendation,
    expire_overdue_recommendations,
    reject_recommendation,
)

router = APIRouter()
logger = logging.getLogger("api.telegram")


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Telegram update receiver. 빠른 200 응답 필수 (재시도 폭주 방지)."""
    if not validate_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="invalid telegram secret")

    payload = await request.json()
    callback = payload.get("callback_query")
    if not callback:
        # 일반 메시지는 무시
        return {"ok": True, "handled": False}

    data = callback.get("data", "")
    parsed = parse_callback_data(data)
    if parsed is None:
        return {"ok": True, "handled": False, "reason": "invalid callback"}

    action, rec_id = parsed
    await expire_overdue_recommendations(session)

    rec = await session.get(AdvisorRecommendation, rec_id)
    if rec is None:
        return {"ok": True, "handled": False, "reason": f"rec {rec_id} not found"}

    try:
        if action == ACTION_APPROVE:
            result = await approve_recommendation(session, rec_id)
            await session.refresh(rec)
            await send_decision_followup(
                rec,
                rec.status,
                detail=(
                    f"trade_plan #{result.get('trade_plan_id')} "
                    f"qty={result.get('shares')}"
                    if rec.status == "approved" and result.get("trade_plan_id")
                    else result.get("message")
                ),
            )
            return {"ok": True, "action": "approve", "result": result}

        if action == ACTION_REJECT:
            # Telegram inline에서 reason 받기는 conversation 필요 — 일단 default
            result = await reject_recommendation(session, rec_id, reason="user_rejected_via_telegram")
            await session.refresh(rec)
            await send_decision_followup(rec, rec.status)
            return {"ok": True, "action": "reject", "result": result}

        if action == ACTION_VIEW:
            return {"ok": True, "action": "view", "reasoning": rec.reasoning_text}

        return {"ok": True, "handled": False}
    except ValueError as exc:
        # rec 없음 → 무시 (사용자에게 노출 안 함)
        logger.warning("[telegram] callback error: %s", exc)
        return {"ok": True, "handled": False, "reason": str(exc)}
    except Exception:
        logger.exception("[telegram] webhook handler crashed")
        return {"ok": True, "handled": False, "reason": "internal_error"}
