"""오늘 trade_plans 현황 (조회만, INSERT/DELETE 없음)."""
from __future__ import annotations

import asyncio
import sys
from datetime import date

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from api.db import async_session_factory  # noqa: E402
from api.db.models import TradePlan  # noqa: E402


async def main() -> None:
    today = date.today()
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(TradePlan)
                .where(TradePlan.plan_date == today)
                .order_by(TradePlan.rank)
            )
        ).scalars().all()
    print(f"오늘({today}) trade_plans: {len(rows)} rows")
    for p in rows:
        bids = p.broker_order_ids or []
        print(
            f"  {p.symbol:6s} dispatch={p.dispatch_mode:11s} "
            f"status={p.confirm_status:10s} "
            f"entry=${p.entry_price} stop=${p.stop_price} t1=${p.target_1r} "
            f"shares={p.shares} broker_ids={len(bids)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
