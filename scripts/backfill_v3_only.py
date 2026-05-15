"""v3만 historical로 backfill (scanner는 별도 백필).

v3는 yfinance fetch가 빠른 편(universe 30종목)이라 10일 분 빠르게 처리 가능.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _trading_days(end_date: date, n: int) -> list[date]:
    days = []
    d = end_date
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


async def run(days: int) -> None:
    from api.db.models import SystemPickLog
    from api.db.session import async_session_factory
    from scanner.comparison.v3_historical import run_v3_for_date
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    target_dates = _trading_days(date.today() - timedelta(days=1), days)
    print(f"v3 historical backfill: {target_dates[0]} ~ {target_dates[-1]} ({len(target_dates)} days)\n")

    sim_per_pick = 10_000.0 / 5

    async with async_session_factory() as session:
        for d in target_dates:
            try:
                picks = await run_v3_for_date(session, d, top=5)
                if not picks:
                    print(f"  {d}: 0 picks")
                    continue
                rows = []
                for p in picks:
                    rows.append(
                        {
                            "system_id": "v3",
                            "pick_date": d,
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
                await session.commit()
                names = ", ".join(p.symbol for p in picks)
                print(f"  {d}: {len(picks)} picks ({names})")
            except Exception as exc:
                print(f"  {d}: ERROR {exc}")

    print("\nRunning outcome backfill...")
    async with async_session_factory() as session:
        from scanner.comparison.outcomes import backfill_pick_outcomes
        result = await backfill_pick_outcomes(session, lookback_days=days + 5)
        print(f"Backfill: {result}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(run(args.days))


if __name__ == "__main__":
    main()
