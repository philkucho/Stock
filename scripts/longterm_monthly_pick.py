"""중장기 monthly pick cron — 월 첫 거래일 09:00 ET 실행.

설계 ([[swing-mode-v1]] 후속, 2026-06-05):
  1. 직전 pick_month의 holdings 조회 (status NOT IN ('exited'))
  2. run_longterm_selection(target_date, prev_holdings=...) 호출
  3. 결과를 longterm_picks 테이블에 upsert
  4. heartbeat Telegram alert (신규/유지/이탈 카운트)

CLI:
    venv/Scripts/python.exe -m scripts.longterm_monthly_pick
    venv/Scripts/python.exe -m scripts.longterm_monthly_pick --date 2026-06-01
    venv/Scripts/python.exe -m scripts.longterm_monthly_pick --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "longterm"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"{date.today().isoformat()}.log", encoding="utf-8"
        ),
    ],
)

logger = logging.getLogger("longterm_monthly_pick")


async def _load_prev_holdings(target_date: date) -> tuple[date | None, dict[str, int]]:
    """직전 pick_month 조회 + symbol → prev_pick_id 매핑."""
    from sqlalchemy import select

    from api.db.models import LongtermPick
    from api.db.session import async_session_factory

    async with async_session_factory() as s:
        stmt = (
            select(LongtermPick.pick_month)
            .where(LongtermPick.pick_month < target_date)
            .order_by(LongtermPick.pick_month.desc())
            .limit(1)
        )
        prev_month = (await s.execute(stmt)).scalar_one_or_none()
        if prev_month is None:
            return None, {}

        stmt2 = (
            select(LongtermPick)
            .where(LongtermPick.pick_month == prev_month)
            .where(LongtermPick.status.in_(["new", "hold", "exit_suggested"]))
        )
        prev = list((await s.execute(stmt2)).scalars().all())
        sym_to_id = {p.symbol: p.id for p in prev}
        return prev_month, sym_to_id


async def main(target_date: date, dry_run: bool = False) -> dict:
    from scanner.longterm.selector import run_longterm_selection

    logger.info("=== longterm_monthly_pick start target=%s dry_run=%s ===",
                target_date, dry_run)

    prev_month, prev_sym_to_id = await _load_prev_holdings(target_date)
    prev_holdings = list(prev_sym_to_id.keys())
    logger.info("[longterm] prev_month=%s prev_holdings=%d",
                prev_month, len(prev_holdings))

    result = await run_longterm_selection(
        target_date, top_n=10, prev_holdings_symbols=prev_holdings,
    )

    new_count = sum(1 for p in result.get("picks", []) if p["status"] == "new")
    hold_count = sum(1 for p in result.get("picks", []) if p["status"] == "hold")
    exit_sug_count = sum(1 for p in result.get("picks", []) if p["status"] == "exit_suggested")
    exited_count = sum(1 for p in result.get("picks", []) if p["status"] == "exited")
    logger.info(
        "[longterm] %s: new=%d hold=%d exit_sug=%d exited=%d defensive=%s",
        target_date, new_count, hold_count, exit_sug_count, exited_count,
        result.get("defensive"),
    )

    if dry_run:
        logger.info("[longterm] DRY RUN — DB write skipped")
        result["dry_run"] = True
        return result

    # DB upsert
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from api.db.models import LongtermPick
    from api.db.session import async_session_factory

    inserted = 0
    async with async_session_factory() as s:
        for p in result.get("picks", []):
            sym = p["symbol"]
            prev_id = prev_sym_to_id.get(sym)
            row = {
                "pick_month": target_date,
                "rank": p["rank"] if p["rank"] is not None else 99,
                "symbol": sym,
                "sector": p.get("sector"),
                "composite_score": Decimal(f"{float(p['composite_score']):.2f}"),
                "gate_results": p.get("gate_results", {}),
                "score_breakdown": p.get("score_breakdown", {}),
                "weight_pct": Decimal(f"{float(p['weight_pct']):.2f}"),
                "status": p["status"],
                "fidelity_action": p["fidelity_action"],
                "prev_pick_id": prev_id,
            }
            stmt = pg_insert(LongtermPick).values(**row)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_longterm_pick_month_sym",
                set_={
                    "rank": stmt.excluded.rank,
                    "composite_score": stmt.excluded.composite_score,
                    "gate_results": stmt.excluded.gate_results,
                    "score_breakdown": stmt.excluded.score_breakdown,
                    "weight_pct": stmt.excluded.weight_pct,
                    "status": stmt.excluded.status,
                    "fidelity_action": stmt.excluded.fidelity_action,
                },
            )
            await s.execute(stmt)
            inserted += 1
        await s.commit()
    logger.info("[longterm] DB upsert: %d rows", inserted)
    result["db_inserted"] = inserted

    # Heartbeat
    try:
        from notifications import send_heartbeat
        send_heartbeat(
            phase="longterm",
            status="done",
            message=(
                f"중장기 monthly: 신규 {new_count} · 유지 {hold_count} "
                f"· 이탈권고 {exit_sug_count} · 청산 {exited_count}"
                f" (regime={'defensive' if result.get('defensive') else 'ok'})"
            ),
            details={
                "target_date": str(target_date),
                "prev_month": str(prev_month) if prev_month else None,
                "new": new_count, "hold": hold_count,
                "exit_suggested": exit_sug_count, "exited": exited_count,
                "candidates_passed": result.get("candidates_passed"),
            },
        )
    except Exception as exc:
        logger.warning("[longterm] heartbeat failed: %s", exc)

    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=lambda s: date.fromisoformat(s), default=None,
                    help="rebalance 기준일 (기본 오늘)")
    ap.add_argument("--dry-run", action="store_true", help="DB write 없이 평가만")
    args = ap.parse_args()
    target = args.date or date.today()
    result = asyncio.run(main(target, dry_run=args.dry_run))
    logger.info(
        "=== done status=%s picks=%d ===",
        result.get("status"), len(result.get("picks", [])),
    )
