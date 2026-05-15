"""Daily pipeline heartbeat — phase 시작/완료/실패 시 이메일 ping.

용도:
  - PC 슬립/리부트로 09:25 task miss 시 "started" ping 부재로 즉시 인지
  - phase 실패 시 stack trace 포함 alert
  - reconciliation alert (broker drift)

활성화:
  .env에 HEARTBEAT_ENABLED=true (기본 false — silent)
  GMAIL_USER / GMAIL_APP_PASSWORD / EMAIL_TO 필요 (이미 daily_email_report에 사용)
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime
from html import escape

from notifications.email_sender import EmailConfigError, send_email

logger = logging.getLogger(__name__)


_STATUS_TAG = {
    "started":   "🟡 STARTED",
    "completed": "✅ DONE",
    "failed":    "❌ FAILED",
    "alert":     "🚨 ALERT",
    "blocked":   "⛔ BLOCKED",
}


def _enabled() -> bool:
    return os.environ.get("HEARTBEAT_ENABLED", "false").strip().lower() == "true"


def _telegram_alert_enabled() -> bool:
    """Telegram alert는 별도 마스터 스위치. TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 모두 있고
    TELEGRAM_ALERT_ENABLED != 'false'일 때 활성. 기본은 활성(키만 있으면 보냄)."""
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        return False
    return os.environ.get("TELEGRAM_ALERT_ENABLED", "true").strip().lower() != "false"


def _suggest_action(error_message: str, phase: str) -> str:
    """에러 메시지 → 한글 조치 가이드. 시스템 alert에 포함되어 사용자가 즉시 대응."""
    m = (error_message or "").lower()
    if "getaddrinfo failed" in m or "could not resolve host" in m or "nameresolution" in m or "dns" in m:
        return (
            "• DNS 해석 실패 — 보통 재부팅 직후 Tailscale DNS hook 안정화 전 일시적\n"
            "• Tailscale 상태 확인: 트레이 아이콘 → Health check에 DNS 경고 있나\n"
            "• 5~10분 후 자동 재시도되며 정상화 가능성 큼\n"
            f"• 안 풀리면 phase 수동 재실행: python -m scripts.daily_pipeline --phase {phase}"
        )
    if "trading_blocked" in m or "account_trading_blocked" in m:
        return (
            "• Alpaca 계정 차단 상태\n"
            "• 콘솔(https://app.alpaca.markets/paper/dashboard) 확인 필요"
        )
    if "insufficient_buying_power" in m or "buying_power" in m:
        return (
            "• 구매력 부족\n"
            "• plan 금액 축소 또는 Alpaca paper Reset"
        )
    if "regime defensive" in m or "long_blocked" in m or "regime.*block" in m:
        return (
            "• Regime defensive mode — 시스템 정상 차단 동작\n"
            "• 시장 회복 시 자동 해제, 추가 조치 불필요"
        )
    if "rate_limit" in m or "429" in m or "resource_exhausted" in m or "quota" in m:
        return (
            "• Rate limit 또는 quota 초과\n"
            "• 잠시 후 재시도 또는 ADVISOR_MODEL을 gemini-2.5-flash로 변경"
        )
    if "permission_denied" in m or "403" in m or "service_disabled" in m:
        return (
            "• API 권한 거부\n"
            "• GCP project에서 Gemini API 활성화 또는 API 키 재발급 확인"
        )
    if "yfinance" in m or "yahoo" in m or "delisted" in m:
        return (
            "• yfinance Yahoo Finance 데이터 fetch 실패 (외부 일시적 장애 가능)\n"
            "• 다음 cron에서 자동 정상화 예상"
        )
    if "broker_order_ids" in m or "place_bracket_order" in m or "order_fail" in m:
        return (
            "• broker 주문 발송 실패\n"
            "• Alpaca 상태 확인 + 가격/qty/buying_power 검증"
        )
    if "drift" in m or "discrepancies" in m or "unexpected_holding" in m:
        return (
            "• Broker 보유 vs DB plan 불일치\n"
            "• /activity 페이지에서 broker 주문 vs plan 비교 권장"
        )
    return (
        "• 자세한 traceback은 logs/daily_pipeline/{날짜}.log 확인\n"
        f"• phase 수동 재실행: python -m scripts.daily_pipeline --phase {phase}"
    )


def _send_telegram_alert(phase: str, status: str, message: str, details: dict | None) -> None:
    """Telegram에 시스템 alert 발송 — failed/alert/blocked만. completed/started는 skip."""
    if status not in ("failed", "alert", "blocked"):
        return
    if not _telegram_alert_enabled():
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return

    tag = _STATUS_TAG.get(status, status.upper())
    err_type = ""
    err_msg = ""
    if details:
        err_type = str(details.get("error_type") or "")
        err_msg = str(details.get("error_message") or "")
    suggestion = _suggest_action(err_msg or message, phase)

    ts = datetime.now().strftime("%H:%M:%S")
    text_lines = [
        f"{tag} <b>daily_pipeline.{phase}</b>",
        f"<i>{ts}</i>",
        "",
    ]
    if message:
        text_lines.append(f"📝 {message}")
    if err_type or err_msg:
        snippet = (err_msg[:300] + "…") if len(err_msg) > 300 else err_msg
        text_lines.append(f"❗ <b>{err_type}</b>: <code>{snippet}</code>")
    text_lines.append("")
    text_lines.append("💡 <b>조치</b>")
    text_lines.append(suggestion)

    body = "\n".join(text_lines)

    try:
        import httpx
        with httpx.Client(timeout=10) as client:
            client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": int(chat_id),
                    "text": body,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except Exception as exc:
        # Telegram 실패는 silent — 본 pipeline 영향 X
        logger.warning("telegram alert failed: %s", exc)


def send_heartbeat(
    phase: str,
    status: str,
    message: str = "",
    details: dict | None = None,
    silent_failure_ok: bool = True,
) -> None:
    """Pipeline phase ping.

    SMTP 실패해도 pipeline 자체는 안 죽이는 게 원칙 (silent_failure_ok=True).
    HEARTBEAT_ENABLED=false면 no-op (logger.info만).
    """
    tag = _STATUS_TAG.get(status, status.upper())
    log_line = f"[heartbeat] {tag} phase={phase} {message}"
    if status in ("started", "completed"):
        logger.info(log_line)
    else:
        logger.warning(log_line)

    # Telegram alert (failed/alert/blocked만, HEARTBEAT_ENABLED와 독립)
    _send_telegram_alert(phase, status, message, details)

    if not _enabled():
        return

    try:
        subject = f"[{tag}] daily_pipeline.{phase}"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        details_json = json.dumps(details or {}, indent=2, default=str)

        html = f"""<!DOCTYPE html>
<html><body style="font-family: sans-serif;">
<h2>{escape(tag)} <code>{escape(phase)}</code></h2>
<p><strong>시각:</strong> {escape(ts)} (PC local)</p>
{f'<p><strong>메시지:</strong> {escape(message)}</p>' if message else ''}
{f'<pre style="background:#f5f5f5;padding:8px;border-radius:4px;">{escape(details_json)}</pre>' if details else ''}
<p style="color:#888;font-size:12px;">자동 발송 — daily_pipeline heartbeat</p>
</body></html>"""

        text = f"{tag} {phase}\n{ts}\n{message}\n\n{details_json}"
        send_email(subject=subject, html_body=html, text_body=text)
    except EmailConfigError as exc:
        logger.warning("heartbeat email skipped (config): %s", exc)
        if not silent_failure_ok:
            raise
    except Exception as exc:
        logger.warning("heartbeat email failed: %s", exc)
        if not silent_failure_ok:
            raise


def send_failure_alert(
    phase: str,
    error: BaseException,
    message: str = "",
    details: dict | None = None,
) -> None:
    """phase 실패 시 stack trace 포함 alert (always sent if HEARTBEAT_ENABLED)."""
    full_details = dict(details or {})
    full_details["error_type"] = type(error).__name__
    full_details["error_message"] = str(error)
    full_details["traceback"] = traceback.format_exc()
    send_heartbeat(phase=phase, status="failed", message=message, details=full_details)


async def reconcile_broker_state(adapter, target_date) -> dict:
    """Alpaca positions/orders vs DB trade_plans 비교.

    불일치 종류:
      - unexpected_holding: DB plan 미발송인데 broker에 포지션 있음 (외부 발송, 잔여 등)
      - mismatch_qty: holding qty != plan.shares (부분 fill 후 잔여 cancel 등)
      - missing_order: plan.broker_order_ids 등록됐는데 open_orders에도 없고 holding으로도
        안 잡혀 있음. 정상 lifecycle(filled→exit→없음)일 수도 있어 alert 발생 X (조용히 표시).
    """
    from sqlalchemy import select

    from api.db.models import TradePlan
    from api.db.session import async_session_factory

    positions = await adapter.get_positions()
    open_orders = await adapter.get_orders(status="open")

    held = {p.symbol.upper(): p for p in positions}

    async with async_session_factory() as session:
        stmt = select(TradePlan).where(TradePlan.plan_date == target_date)
        plans = list((await session.execute(stmt)).scalars().all())

    plans_by_sym = {p.symbol.upper(): p for p in plans}
    discrepancies: list[dict] = []

    # 1) broker에 있는데 DB plan 없음 — 외부 발송 또는 전일 잔여
    for sym, pos in held.items():
        if sym not in plans_by_sym:
            discrepancies.append({
                "type": "unexpected_holding",
                "symbol": sym,
                "qty": pos.qty,
                "avg_entry": pos.avg_entry_price,
            })

    # 2) plan과 holding qty 불일치 정확 계산 (1.1 강화).
    # expected_holding = (entry_filled_1 + entry_filled_2) - (filled_qty_1r + filled_qty_2r)
    # entry_filled가 None(아직 fill_sync 안 돈 상태)이면 plan.shares를 fallback으로 사용 (보수적).
    for sym, plan in plans_by_sym.items():
        if sym not in held:
            continue
        held_qty = held[sym].qty
        plan_qty = int(plan.shares)
        is_two_tier = bool(plan.broker_order_ids) and len(plan.broker_order_ids) >= 2

        ef_1 = plan.entry_filled_qty_1
        ef_2 = plan.entry_filled_qty_2
        sf_1 = plan.filled_qty_1r or 0
        sf_2 = plan.filled_qty_2r or 0

        if ef_1 is None or ef_2 is None:
            # fill_sync 미실행 상태 — 보수적 fallback (구 로직).
            partial_qty = plan_qty // 2 if is_two_tier else 0
            allowed_qtys = {0, plan_qty}
            if is_two_tier:
                allowed_qtys.add(partial_qty)
            if held_qty not in allowed_qtys:
                discrepancies.append({
                    "type": "mismatch_qty",
                    "symbol": sym,
                    "broker_qty": held_qty,
                    "plan_qty": plan_qty,
                    "expected_one_of": sorted(allowed_qtys),
                    "is_two_tier": is_two_tier,
                    "note": "fill_sync_pending — using plan.shares fallback",
                })
            continue

        # fill_sync 적용 후 — 정확한 expected 계산
        entry_filled_total = ef_1 + ef_2
        sell_filled_total = sf_1 + sf_2
        expected_holding = entry_filled_total - sell_filled_total

        if held_qty != expected_holding:
            entry_partial = (ef_1 < plan_qty // 2 if is_two_tier else ef_1 < plan_qty) or \
                            (is_two_tier and ef_2 < plan_qty - (plan_qty // 2))
            discrepancies.append({
                "type": "mismatch_qty",
                "symbol": sym,
                "broker_qty": held_qty,
                "expected_holding": expected_holding,
                "entry_filled": [ef_1, ef_2],
                "sell_filled": [sf_1, sf_2],
                "plan_qty": plan_qty,
                "entry_partial": entry_partial,
            })

    return {
        "target_date": target_date.isoformat(),
        "broker_positions": len(positions),
        "broker_open_orders": len(open_orders),
        "db_plans": len(plans),
        "discrepancies": discrepancies,
    }
