"""Daily picks 로거 — 매일 개장 직전 (09:30 ET 약간 전) 호출.

3 시스템 각각의 top 5 picks를 system_pick_logs 테이블에 기록.
같은 (system_id, pick_date, symbol) 충돌 시 upsert (rank/score만 갱신).
entry_price는 다음날 16:30 backfill에서 채움.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import SystemPickLog
from scanner.comparison import SIM_CAPITAL_PER_SYSTEM, TOP_N
from scanner.comparison.adapters import fetch_all_systems

logger = logging.getLogger(__name__)


async def log_daily_picks(session: AsyncSession, target_date: date | None = None) -> dict:
    """매일 호출. 3 시스템 picks를 system_pick_logs에 upsert.

    반환: {"v3": n_logged, "scanner": n_logged, "integrated": n_logged, "errors": [...]}
    """
    if target_date is None:
        target_date = date.today()

    all_picks = await fetch_all_systems(session, target_date, top=TOP_N)
    sim_per_pick = SIM_CAPITAL_PER_SYSTEM / TOP_N  # $2,000

    summary: dict[str, int] = {}
    for system_id, picks in all_picks.items():
        if not picks:
            summary[system_id] = 0
            logger.info("[%s] no picks for %s", system_id, target_date)
            continue
        rows = []
        for p in picks[:TOP_N]:
            rows.append(
                {
                    "system_id": system_id,
                    "pick_date": target_date,
                    "rank": p.rank,
                    "symbol": p.symbol,
                    "score": Decimal(f"{p.score:.2f}"),
                    "score_meta": p.score_meta,
                    "sector": p.sector,
                    "strategy_tag": p.strategy_tag,
                    "sim_capital_usd": Decimal(str(sim_per_pick)),
                }
            )
        stmt = pg_insert(SystemPickLog).values(rows)
        update_cols = {
            "rank": stmt.excluded.rank,
            "score": stmt.excluded.score,
            "score_meta": stmt.excluded.score_meta,
            "sector": stmt.excluded.sector,
            "strategy_tag": stmt.excluded.strategy_tag,
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_pick_log_sys_date_sym", set_=update_cols
        )
        await session.execute(stmt)
        summary[system_id] = len(rows)
        logger.info("[%s] logged %d picks for %s", system_id, len(rows), target_date)

    await session.commit()
    return summary
