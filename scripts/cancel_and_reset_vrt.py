"""오늘 VRT 발송 brackets cancel + DB broker_order_ids reset (재발송 준비)."""
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
from broker_adapter import get_adapter  # noqa: E402


async def main() -> None:
    today = date.today()
    async with async_session_factory() as s:
        row = (
            await s.execute(
                select(TradePlan)
                .where(TradePlan.plan_date == today)
                .where(TradePlan.symbol == "VRT")
            )
        ).scalar_one_or_none()
        if row is None:
            print("VRT plan not found")
            return
        ids = list(row.broker_order_ids or [])
        print(f"VRT broker_order_ids in DB: {ids}")

        if ids:
            adapter = get_adapter()
            try:
                for oid in ids:
                    ok = await adapter.cancel_order(oid)
                    print(f"  cancel {oid[:12]}... -> {'ok' if ok else 'failed'}")
            finally:
                await adapter.close()

        # DB reset
        row.broker_order_ids = []
        await s.commit()
        print("DB broker_order_ids reset to [] — 재발송 가능 상태")


if __name__ == "__main__":
    asyncio.run(main())
