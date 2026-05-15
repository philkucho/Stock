"""Hybrid dispatch 검증 — 가짜 plan 2개 INSERT 후 cron filter 동작 확인 → 삭제.

목적:
1. dispatch_mode='user_fixed' plan은 run_trade만 잡는다
2. dispatch_mode='orb_auto'+confirm_status='watchlist' plan은 run_confirm만 잡는다
3. 서로 교차 오염 없음

DB write 안전성:
- 가짜 symbol (_TEST_USR, _TEST_ORB) — 실제 ticker와 충돌 없음
- 종료 시 삭제 (try/finally)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from decimal import Decimal

from dotenv import load_dotenv
from sqlalchemy import delete, select

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from api.db import async_session_factory  # noqa: E402
from api.db.models import TradePlan  # noqa: E402


async def main() -> None:
    today = date.today()
    test_symbols = ["_TEST_USR", "_TEST_ORB"]

    async with async_session_factory() as s:
        # 1) 사전 정리 (이전 실행 잔여 row가 있으면 제거)
        await s.execute(delete(TradePlan).where(TradePlan.symbol.in_(test_symbols)))
        await s.commit()

        # 2) 가짜 plan 2개 INSERT
        user_plan = TradePlan(
            plan_date=today,
            symbol="_TEST_USR",
            rank=98,
            amount_usd=Decimal("100.00"),
            entry_price=Decimal("10.0000"),
            stop_price=Decimal("9.0000"),
            target_1r=Decimal("11.0000"),
            target_2r=Decimal("12.0000"),
            composite_score=Decimal("0.50"),
            sector="TEST",
            shares=10,
            risk_usd=Decimal("10.00"),
            score_meta={"test": True},
            dispatch_mode="user_fixed",
            confirm_status="watchlist",
        )
        orb_plan = TradePlan(
            plan_date=today,
            symbol="_TEST_ORB",
            rank=99,
            amount_usd=Decimal("100.00"),
            entry_price=Decimal("20.0000"),
            stop_price=Decimal("19.0000"),
            target_1r=Decimal("21.0000"),
            target_2r=Decimal("22.0000"),
            composite_score=Decimal("0.50"),
            sector="TEST",
            shares=5,
            risk_usd=Decimal("5.00"),
            score_meta={"test": True},
            dispatch_mode="orb_auto",
            confirm_status="watchlist",
        )
        s.add_all([user_plan, orb_plan])
        await s.commit()

        try:
            # 3) run_trade가 사용하는 SELECT (daily_pipeline.py:493-500 그대로)
            trade_q = (
                select(TradePlan)
                .where(TradePlan.plan_date == today)
                .where(TradePlan.dispatch_mode == "user_fixed")
                .order_by(TradePlan.rank)
            )
            trade_rows = (await s.execute(trade_q)).scalars().all()
            print("== run_trade SELECT (WHERE dispatch_mode='user_fixed') ==")
            for r in trade_rows:
                print(f"   matched: {r.symbol} ({r.dispatch_mode}/{r.confirm_status})")
            user_only = [r.symbol for r in trade_rows]
            assert "_TEST_USR" in user_only, "FAIL: user_fixed plan을 못 잡음"
            assert "_TEST_ORB" not in user_only, "FAIL: orb_auto plan이 user_fixed 필터를 통과함!"
            print("   OK — user_fixed 만 매칭, orb_auto 제외 확인")

            # 4) run_confirm가 사용하는 SELECT (intraday_confirm.py:139-146 그대로)
            confirm_q = (
                select(TradePlan)
                .where(TradePlan.plan_date == today)
                .where(TradePlan.confirm_status == "watchlist")
                .where(TradePlan.dispatch_mode == "orb_auto")
                .order_by(TradePlan.rank)
            )
            confirm_rows = (await s.execute(confirm_q)).scalars().all()
            print()
            print("== run_confirm SELECT (WHERE confirm_status='watchlist' AND dispatch_mode='orb_auto') ==")
            for r in confirm_rows:
                print(f"   matched: {r.symbol} ({r.dispatch_mode}/{r.confirm_status})")
            orb_only = [r.symbol for r in confirm_rows]
            assert "_TEST_ORB" in orb_only, "FAIL: orb_auto+watchlist plan을 못 잡음"
            assert "_TEST_USR" not in orb_only, "FAIL: user_fixed plan이 orb_auto 필터를 통과함!"
            print("   OK — orb_auto+watchlist 만 매칭, user_fixed 제외 확인")

            print()
            print("=== 결과: 두 cron 필터가 서로 격리됨 ===")
            print("  09:30 run_trade  -> dispatch_mode='user_fixed' plan만 발송")
            print("  09:45 run_confirm -> dispatch_mode='orb_auto' watchlist plan만 ORB 평가/발송")
        finally:
            # 5) 테스트 row 삭제 (실제 cron이 가짜 발송 시도하지 않도록)
            await s.execute(delete(TradePlan).where(TradePlan.symbol.in_(test_symbols)))
            await s.commit()
            print()
            print("(테스트 row 삭제 완료)")


if __name__ == "__main__":
    asyncio.run(main())
