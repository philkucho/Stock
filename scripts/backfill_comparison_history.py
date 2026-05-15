"""과거 N일 통합 picks 로그 + outcome 백필.

scan_momentum은 historical bars만 있으면 어떤 과거 날짜든 picks 산출 가능.
v3는 daily_picks 테이블 의존이라 sparse.

사용:
    python -m scripts.backfill_comparison_history --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()


def _trading_days(end_date: date, n: int) -> list[date]:
    """end_date에서 거꾸로 n 거래일 (월~금)."""
    days: list[date] = []
    d = end_date
    while len(days) < n:
        if d.weekday() < 5:  # Mon=0..Fri=4
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


async def run(days: int) -> None:
    from api.db.session import async_session_factory
    from scanner.comparison.logger import log_daily_picks
    from scanner.comparison.outcomes import backfill_pick_outcomes

    today = date.today()
    target_dates = _trading_days(today - timedelta(days=1), days)  # 어제까지
    print(f"Backfilling {len(target_dates)} trading days: {target_dates[0]} ~ {target_dates[-1]}\n")

    log_summary = {"v3": 0, "scanner": 0, "integrated": 0}
    async with async_session_factory() as session:
        for d in target_dates:
            try:
                result = await log_daily_picks(session, d)
                for k, v in result.items():
                    log_summary[k] = log_summary.get(k, 0) + v
                v3 = result.get("v3", 0)
                sc = result.get("scanner", 0)
                print(f"  {d}: v3={v3}, scanner={sc}")
            except Exception as exc:
                print(f"  {d}: ERROR {exc}")

    print(f"\nLog totals: {log_summary}\n")

    # 백필 — 충분히 큰 lookback으로 모든 historical picks 커버
    print("Running outcome backfill (1d/5d/10d)...")
    async with async_session_factory() as session:
        result = await backfill_pick_outcomes(session, target_date=today, lookback_days=days + 5)
        print(f"Backfill: {result}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="과거 거래일 수 (default: 30)")
    args = parser.parse_args()
    asyncio.run(run(args.days))


if __name__ == "__main__":
    main()
