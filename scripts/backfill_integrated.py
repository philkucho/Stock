"""Integrated system 30일 historical backfill.

scanner_picks (각 날짜) + v3 quality layer 적용 → composite top 5.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _trading_days(end_date: date, n: int) -> list[date]:
    days: list[date] = []
    d = end_date
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


async def run(days: int) -> None:
    import asyncio as _asyncio

    from api.db.models import SystemPickLog
    from api.db.session import async_session_factory
    from scanner.comparison.adapters import fetch_scanner_picks
    from scanner.comparison.v3_historical import run_v3_for_date
    from scanner.integrated.run import run_integrated_v10
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    target_dates = _trading_days(date.today() - timedelta(days=1), days)
    print(f"Integrated backfill: {target_dates[0]} ~ {target_dates[-1]} ({len(target_dates)} days)\n")

    sim_per_pick = 10_000.0 / 5

    # ── 최적화 1: scanner pool — DB의 system_pick_logs에서 재사용 (이미 30일치 적재됨) ──
    print(f"[optim] Loading scanner pools from DB cache for {len(target_dates)} dates...")
    from sqlalchemy import select
    from api.db.models import SystemPickLog
    from scanner.comparison.adapters import PickCandidate

    scanner_pool: dict = {}
    async with async_session_factory() as session:
        stmt = select(SystemPickLog).where(
            SystemPickLog.system_id == "scanner",
            SystemPickLog.pick_date >= target_dates[0],
            SystemPickLog.pick_date <= target_dates[-1],
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        for r in rows:
            pc = PickCandidate(
                system_id="scanner",
                rank=r.rank,
                symbol=r.symbol,
                score=float(r.score),
                score_meta=r.score_meta or {},
                sector=r.sector,
                strategy_tag=r.strategy_tag,
            )
            scanner_pool.setdefault(r.pick_date, []).append(pc)
    for d in target_dates:
        scanner_pool.setdefault(d, [])
    n_with_pool = sum(1 for v in scanner_pool.values() if v)
    print(f"[optim] Scanner pool ready: {n_with_pool}/{len(target_dates)} dates have candidates (DB cached)")

    # ── 최적화 2: v3 pool 병렬 사전 계산 (semaphore로 동시 25개 제한 — connection pool 안전) ──
    print(f"[optim] Pre-computing v3 pools for {len(target_dates)} dates (concurrency=25)...")

    sem = _asyncio.Semaphore(25)

    async def _v3_with_session(d):
        async with sem:
            async with async_session_factory() as s:
                return await run_v3_for_date(s, d, top=15)

    v3_pool: dict = {}
    v3_tasks = [_v3_with_session(d) for d in target_dates]
    v3_results = await _asyncio.gather(*v3_tasks, return_exceptions=True)
    for d, res in zip(target_dates, v3_results):
        if isinstance(res, Exception):
            print(f"  [warn] {d} v3 fail: {res}")
            v3_pool[d] = []
        else:
            v3_pool[d] = res
    n_v3 = sum(1 for v in v3_pool.values() if v)
    print(f"[optim] V3 pool ready: {n_v3}/{len(target_dates)} dates have candidates\n")

    # ── 최적화 3: v3 quality layer 적용 (sequential — 캐시 활용으로 빠름) ──
    async with async_session_factory() as session:
        for d in target_dates:
            try:
                cached_sc = scanner_pool.get(d, [])
                cached_v3 = v3_pool.get(d, [])
                picks = await run_integrated_v10(
                    d, top=5,
                    session=session,
                    scanner_picks_cached=cached_sc,
                    v3_picks_cached=cached_v3,
                )
                if not picks:
                    print(f"  {d}: 0 picks")
                    continue
                rows = []
                for p in picks:
                    rows.append(
                        {
                            "system_id": "integrated",
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
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(run(args.days))


if __name__ == "__main__":
    main()
