"""position_cap 점진 체크 검증 — 6개 user_fixed plan INSERT 후 dry-run으로 5개만 발송되는지 확인.

AUTO_TRADE_ENABLED=false 환경에서 실행 → adapter가 dry_run Order 반환, 실제 Alpaca 발송 X.
종료 시 테스트 row 삭제 (try/finally).
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from decimal import Decimal

from dotenv import load_dotenv
from sqlalchemy import delete, select

load_dotenv()
# 명시적 OFF — 실 Alpaca 발송 안 함
os.environ["AUTO_TRADE_ENABLED"] = "false"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from api.db import async_session_factory  # noqa: E402
from api.db.models import TradePlan  # noqa: E402
from scripts.daily_pipeline import run_trade  # noqa: E402


async def main() -> None:
    today = date.today()
    test_symbols = [f"_TC{i}" for i in range(1, 7)]  # _TC1.._TC6 (6개)

    async with async_session_factory() as s:
        # 사전 정리
        await s.execute(delete(TradePlan).where(TradePlan.symbol.in_(test_symbols)))
        # 진짜 VRT가 있으면 잠시 dispatch_mode를 orb_auto로 옮겨서 run_trade가 무시하도록
        vrt_row = (
            await s.execute(
                select(TradePlan).where(TradePlan.plan_date == today).where(TradePlan.symbol == "VRT")
            )
        ).scalar_one_or_none()
        vrt_orig_mode = vrt_row.dispatch_mode if vrt_row else None
        vrt_orig_ids = list(vrt_row.broker_order_ids or []) if vrt_row else None
        if vrt_row:
            vrt_row.dispatch_mode = "orb_auto"
            vrt_row.broker_order_ids = []  # 테스트 중 멱등 skip 영향 제거
        await s.commit()

        # 6개 user_fixed plan INSERT (sector 분산 → sector_cap 영향 제거)
        sectors = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
        rows = []
        for i, sym in enumerate(test_symbols):
            rows.append(
                TradePlan(
                    plan_date=today,
                    symbol=sym,
                    rank=90 + i,
                    amount_usd=Decimal("100.00"),
                    entry_price=Decimal("10.0000"),
                    stop_price=Decimal("9.0000"),
                    target_1r=Decimal("11.0000"),
                    target_2r=Decimal("12.0000"),
                    composite_score=Decimal("0.50"),
                    sector=sectors[i],
                    shares=2,
                    risk_usd=Decimal("2.00"),
                    score_meta={"test": True},
                    dispatch_mode="user_fixed",
                    confirm_status="watchlist",
                )
            )
        s.add_all(rows)
        await s.commit()

        try:
            print("=== run_trade (AUTO_TRADE_ENABLED=false, dry-run) ===")
            result = await run_trade(today, position_cap=5)
            print(f"status={result.get('status')}")
            print(f"plans_count={result.get('plans_count')}")
            print(f"orders sent = {len(result.get('orders', []))}")
            for o in result.get("orders", []):
                print(f"  ✓ {o['symbol']}")
            print(f"skipped = {len(result.get('skipped', []))}")
            for sk in result.get("skipped", []):
                print(f"  ✗ {sk['symbol']}: {sk['reason']}")

            n_sent = len(result.get("orders", []))
            n_skipped_cap = sum(
                1 for sk in result.get("skipped", [])
                if "position_cap_reached" in sk.get("reason", "")
            )
            print()
            if n_sent == 5 and n_skipped_cap == 1:
                print(f"PASS — sent={n_sent} (==5), skipped(position_cap)={n_skipped_cap} (==1)")
            else:
                print(f"FAIL — sent={n_sent} (expected 5), skipped(position_cap)={n_skipped_cap} (expected 1)")

        finally:
            # 테스트 row 삭제 + VRT 원복
            await s.execute(delete(TradePlan).where(TradePlan.symbol.in_(test_symbols)))
            if vrt_row:
                v2 = (
                    await s.execute(
                        select(TradePlan).where(TradePlan.plan_date == today).where(TradePlan.symbol == "VRT")
                    )
                ).scalar_one_or_none()
                if v2:
                    v2.dispatch_mode = vrt_orig_mode
                    v2.broker_order_ids = vrt_orig_ids
            await s.commit()
            print()
            print("(테스트 row 삭제 + VRT 원복 완료)")


if __name__ == "__main__":
    asyncio.run(main())
