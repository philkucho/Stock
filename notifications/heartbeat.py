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
