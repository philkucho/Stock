"""소급 fix: broker_order_ids 있는데 confirm_status='watchlist' stuck인 plan들 → 'sent'.

2026-05-15 발견된 daily_pipeline.run_trade의 status 누락 버그로 인한 잔여 데이터 정정.
"""
from __future__ import annotations

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()


async def main() -> None:
    from sqlalchemy import select
    from api.db.session import async_session_factory
    from api.db.models import TradePlan

    async with async_session_factory() as s:
        # broker_order_ids 있는데 status='watchlist'인 행 조회
        stmt = (
            select(TradePlan)
            .where(TradePlan.broker_order_ids.is_not(None))
            .where(TradePlan.confirm_status == "watchlist")
            .order_by(TradePlan.plan_date)
        )
        stuck = list((await s.execute(stmt)).scalars().all())

        if not stuck:
            print("[fix_sent_status] 대상 없음 (이미 모두 정상)")
            return

        print(f"=== 대상 {len(stuck)}건 ===")
        for p in stuck:
            print(
                f"  id={p.id} {p.plan_date} {p.symbol} mode={p.dispatch_mode} "
                f"qty={p.shares} entry=${float(p.entry_price):.2f} "
                f"broker_ids={len(p.broker_order_ids)}"
            )

        for p in stuck:
            p.confirm_status = "sent"
        await s.commit()
        print(f"\n[fix_sent_status] confirm_status='sent'로 갱신 완료: {len(stuck)}건")


if __name__ == "__main__":
    asyncio.run(main())
