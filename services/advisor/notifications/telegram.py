"""Telegram bot integration — inline approve/reject keyboard.

봇 셋업:
  1. @BotFather에 /newbot — token 발급
  2. 봇과 채팅 시작 후 https://api.telegram.org/bot<TOKEN>/getUpdates 호출해 chat_id 확인
  3. .env에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_WEBHOOK_SECRET 입력
  4. webhook 등록:
       curl -F "url=https://<your-domain>/api/telegram/webhook" \
            -F "secret_token=<TELEGRAM_WEBHOOK_SECRET>" \
            https://api.telegram.org/bot<TOKEN>/setWebhook

callback_data 형식: "adv:<action>:<rec_id>"
  adv:ap:123  → approve recommendation #123
  adv:rj:123  → reject recommendation #123
  adv:vw:123  → view full details (reasoning)
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AdvisorRecommendation

logger = logging.getLogger("advisor.telegram")

CALLBACK_PREFIX = "adv"
ACTION_APPROVE = "ap"
ACTION_REJECT = "rj"
ACTION_VIEW = "vw"


def _config() -> dict[str, str]:
    """환경변수에서 봇 token/chat_id/secret 로드."""
    return {
        "token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        "secret": os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip(),
    }


def _bot_enabled() -> bool:
    cfg = _config()
    return bool(cfg["token"] and cfg["chat_id"])


async def _get_bot():
    """python-telegram-bot Bot 인스턴스. async context manager 아님 — 직접 close 필요 없음."""
    from telegram import Bot

    cfg = _config()
    if not cfg["token"]:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    return Bot(token=cfg["token"])


def _format_recommendation(rec: AdvisorRecommendation) -> str:
    """추천 → Telegram 메시지 텍스트 (Markdown V2 escape는 생략, HTML 사용)."""
    icon = {
        "morning": "🌅",
        "intraday_entry": "🟢",
        "intraday_add": "➕",
        "intraday_exit": "🔴",
    }.get(rec.rec_type, "💡")

    conf_pct = int(float(rec.confidence) * 100) if rec.confidence else 0
    entry = float(rec.entry_price) if rec.entry_price else None
    stop = float(rec.stop_price) if rec.stop_price else None
    t1 = float(rec.target_1r) if rec.target_1r else None
    t2 = float(rec.target_2r) if rec.target_2r else None

    risk_pct = None
    if entry and stop and entry > 0:
        risk_pct = (entry - stop) / entry * 100.0
    rr = None
    if entry and stop and t1 and (entry - stop) > 0:
        rr = (t1 - entry) / (entry - stop)

    lines = [
        f"{icon} <b>{rec.symbol}</b> · {rec.rec_type.replace('_', ' ')}",
        f"confidence: <b>{conf_pct}%</b> · expires in 5min",
        "",
    ]
    if entry:
        lines.append(f"📍 entry: <code>${entry:.2f}</code>")
    if stop:
        risk_str = f" ({risk_pct:.1f}%)" if risk_pct else ""
        lines.append(f"🛑 stop: <code>${stop:.2f}</code>{risk_str}")
    if t1:
        rr_str = f" (R:R {rr:.2f})" if rr else ""
        lines.append(f"🎯 1R: <code>${t1:.2f}</code>{rr_str}")
    if t2:
        lines.append(f"🎯 2R: <code>${t2:.2f}</code>")
    if rec.qty:
        lines.append(f"📦 qty: <b>{rec.qty}</b>")

    lines.append("")
    reasoning = (rec.reasoning_text or "").strip()
    if len(reasoning) > 600:
        reasoning = reasoning[:600] + "..."
    if reasoning:
        lines.append(f"<i>{_escape_html(reasoning)}</i>")

    return "\n".join(lines)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_keyboard(rec_id: int) -> Any:
    """python-telegram-bot InlineKeyboardMarkup. 지연 import."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                text="✅ Approve",
                callback_data=f"{CALLBACK_PREFIX}:{ACTION_APPROVE}:{rec_id}",
            ),
            InlineKeyboardButton(
                text="❌ Reject",
                callback_data=f"{CALLBACK_PREFIX}:{ACTION_REJECT}:{rec_id}",
            ),
        ],
    ])


async def send_recommendation_notification(
    session: AsyncSession, rec_id: int
) -> int | None:
    """추천을 Telegram 채팅으로 전송. 반환: message_id (성공 시) | None.

    토큰/chat_id 미설정이면 silent skip (logger.info).
    """
    if not _bot_enabled():
        logger.info("[telegram] bot not configured — skipped notification for rec=%d", rec_id)
        return None

    rec = await session.get(AdvisorRecommendation, rec_id)
    if rec is None:
        return None

    cfg = _config()
    text = _format_recommendation(rec)
    keyboard = _build_keyboard(rec.id)

    try:
        bot = await _get_bot()
        sent = await bot.send_message(
            chat_id=cfg["chat_id"],
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        rec.telegram_message_id = sent.message_id
        await session.commit()
        return sent.message_id
    except Exception as exc:
        logger.warning("[telegram] send failed for rec=%d: %s", rec_id, exc)
        return None


async def send_decision_followup(
    rec: AdvisorRecommendation,
    decision: str,
    detail: str | None = None,
) -> None:
    """승인/거부 확정 시 follow-up 메시지 (편집).

    원본 inline keyboard를 회색 처리해서 중복 클릭 방지.
    """
    if not _bot_enabled() or not rec.telegram_message_id:
        return

    cfg = _config()
    badge = {
        "approved": "✅ Approved",
        "rejected": "❌ Rejected",
        "expired": "⌛ Expired",
    }.get(decision, decision)
    footer = f"\n\n<b>{badge}</b>"
    if detail:
        footer += f" — {_escape_html(detail)}"

    try:
        bot = await _get_bot()
        await bot.edit_message_text(
            chat_id=cfg["chat_id"],
            message_id=rec.telegram_message_id,
            text=_format_recommendation(rec) + footer,
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception as exc:
        logger.warning("[telegram] edit failed for rec=%d: %s", rec.id, exc)


def parse_callback_data(data: str) -> tuple[str, int] | None:
    """callback_data → (action, rec_id) | None on invalid."""
    if not data or not data.startswith(f"{CALLBACK_PREFIX}:"):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    _, action, rec_id_str = parts
    try:
        rec_id = int(rec_id_str)
    except ValueError:
        return None
    if action not in (ACTION_APPROVE, ACTION_REJECT, ACTION_VIEW):
        return None
    return action, rec_id


def validate_secret(provided_token: str | None) -> bool:
    """Webhook secret_token (X-Telegram-Bot-Api-Secret-Token 헤더) 검증.

    빈 secret 설정 시 검증 skip (개발 편의).
    """
    cfg = _config()
    expected = cfg["secret"]
    if not expected:
        return True
    return provided_token == expected
