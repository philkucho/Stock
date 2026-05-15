"""오늘 VRT plan을 user_fixed로 승격 (수동 시연용 일회성).

기존 entry/stop/target/shares는 그대로 두고 dispatch_mode + confirm_status만 변경.
"""
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
        row = (
            await s.execute(
                select(TradePlan)
                .where(TradePlan.plan_date == today)
                .where(TradePlan.symbol == "VRT")
            )
        ).scalar_one_or_none()
        if row is None:
            print("VRT plan not found for today")
            return
        print(f"BEFORE: {row.symbol} dispatch={row.dispatch_mode} status={row.confirm_status}")
        row.dispatch_mode = "user_fixed"
        row.confirm_status = "watchlist"
        # 이전 ORB 실패 사유는 score_meta에 남아있으니 깨끗하게 정리
        meta = dict(row.score_meta or {})
        meta.pop("confirm_fail_reasons", None)
        meta.pop("orb_evaluation", None)
        meta["promoted_to_user_fixed"] = today.isoformat()
        row.score_meta = meta
        await s.commit()
        print(f"AFTER:  {row.symbol} dispatch={row.dispatch_mode} status={row.confirm_status}")


if __name__ == "__main__":
    asyncio.run(main())
