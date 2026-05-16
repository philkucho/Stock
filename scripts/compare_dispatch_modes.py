"""dispatch_mode 별 (user_fixed vs orb_auto) 수익성 비교 + 발송 진단."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from statistics import mean

from dotenv import load_dotenv
load_dotenv()


async def main() -> None:
    from sqlalchemy import select, func
    from api.db.session import async_session_factory
    from api.db.models import TradePlan, TradePlanOutcome

    async with async_session_factory() as s:
        # 1) 모든 plan 상세 덤프
        plans = (await s.execute(
            select(TradePlan).order_by(TradePlan.plan_date.desc(), TradePlan.created_at.desc())
        )).scalars().all()
        print(f"=== Total TradePlans: {len(plans)} ===")
        for p in plans:
            bids = p.broker_order_ids
            bids_str = f"{len(bids) if bids else 0} ids" if bids else "no ids"
            print(
                f"  {p.plan_date} {p.symbol:6s} mode={p.dispatch_mode:10s} "
                f"status={p.confirm_status:10s} qty={p.shares} entry=${float(p.entry_price):.2f} "
                f"broker={bids_str} risk=${float(p.risk_usd):.0f}"
            )
            if bids:
                print(f"     broker_order_ids: {bids}")

        # 2) preopen이 만든 system_pick_logs (orb_auto 후보 원천)
        print()
        try:
            from api.db.models import SystemPickLog
            pick_rows = (await s.execute(
                select(SystemPickLog.system_id, func.count(), func.min(SystemPickLog.pick_date),
                       func.max(SystemPickLog.pick_date))
                .group_by(SystemPickLog.system_id)
            )).all()
            print("=== SystemPickLog (preopen 출처) ===")
            for sys_name, cnt, mn, mx in pick_rows:
                print(f"  {sys_name:24s} n={cnt}  range={mn}~{mx}")
        except Exception as exc:
            print(f"  (SystemPickLog query failed: {exc})")

        # 3) pick_outcomes — 시스템 picks의 hypothetical 1d/5d/10d
        try:
            from api.db.models import PickOutcome
            po_rows = (await s.execute(
                select(SystemPickLog.system_id, PickOutcome.horizon_days, func.count(),
                       func.avg(PickOutcome.pct_return), func.avg(PickOutcome.alpha))
                .join(PickOutcome, PickOutcome.pick_log_id == SystemPickLog.id)
                .group_by(SystemPickLog.system_id, PickOutcome.horizon_days)
                .order_by(SystemPickLog.system_id, PickOutcome.horizon_days)
            )).all()
            print("\n=== pick_outcomes — hypothetical returns by system ===")
            print(f"  {'system':<24s} {'h':>3s} {'n':>4s} {'avg_ret%':>10s} {'avg_alpha%':>12s}")
            for sys_name, h, n, ret, alpha in po_rows:
                ret_v = float(ret) if ret is not None else 0
                alpha_v = float(alpha) if alpha is not None else 0
                print(f"  {sys_name:<24s} {h:>3d} {n:>4d} {ret_v:>10.3f} {alpha_v:>12.3f}")
        except Exception as exc:
            print(f"  (PickOutcome query failed: {exc})")


if __name__ == "__main__":
    asyncio.run(main())
