"""APScheduler 기반 단타 스캐너 cron.

Jobs (US/Eastern):
  - 매월 1일 04:00 ET: stage1_universe.refresh() + rolling_revalidate()
  - 매일 (월~금) 08:55 ET: stage2_daily_picks.run_daily_picks()
  - 매일 (월~금) 09:15 ET: daily_email_report (premarket 데이터 충분히 누적된 시점)
  - 매일 (월~금) 09:25 ET: 3 시스템 통합 비교 picks 기록
  - 매일 (월~금) 16:30 ET: 당일 picks 결과 백필 (Phase 3)
  - 매일 (월~금) 16:35 ET: 1d/5d/10d outcome 백필

paper 단계에서는 cron 없이 API 수동 트리거 (POST /api/picks/refresh) 권장.
실전 운영 시 `python -m scanner.scheduler` 또는 systemd unit으로 데몬 실행.

DST는 APScheduler가 자동 처리 (timezone="US/Eastern").
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

ET_TZ = "US/Eastern"


async def run_stage1() -> None:
    from api.db.session import async_session_factory
    from scanner.stage1_universe import refresh

    async with async_session_factory() as session:
        result = await refresh(session)
        logger.info("Stage1 cron complete: %s", result)


async def run_stage2() -> None:
    from api.db.session import async_session_factory
    from scanner.stage2_daily_picks import run_daily_picks

    async with async_session_factory() as session:
        picks = await run_daily_picks(session, date.today())
        logger.info("Stage2 cron complete: %d picks", len(picks))


async def run_outcome_backfill() -> None:
    """당일 picks의 실제 결과 (시초가→종가 수익률) 백필. Phase 3에서 활용."""
    logger.info("Outcome backfill TODO (Phase 3)")


async def run_comparison_log() -> None:
    """매일 09:30 ET 직전 — 3 시스템 picks를 통합 비교 로그에 기록."""
    from api.db.session import async_session_factory
    from scanner.comparison.logger import log_daily_picks

    async with async_session_factory() as session:
        result = await log_daily_picks(session)
        logger.info("Comparison log: %s", result)


async def run_comparison_backfill() -> None:
    """매일 16:35 ET — system_pick_logs + trade_plans 모두 1d/5d/10d outcome 백필."""
    from api.db.session import async_session_factory
    from scanner.comparison.outcomes import (
        backfill_pick_outcomes,
        backfill_trade_plan_outcomes,
    )

    async with async_session_factory() as session:
        result = await backfill_pick_outcomes(session, lookback_days=30)
        logger.info("Comparison backfill (system_pick_logs): %s", result)
        trade_result = await backfill_trade_plan_outcomes(session, lookback_days=30)
        logger.info("Comparison backfill (trade_plans): %s", trade_result)


async def run_daily_email() -> None:
    """매일 09:00 ET — 종합 리포트 이메일 발송 (regime + picks + scanner + 전일 PnL)."""
    try:
        from scripts.daily_email_report import generate_and_send  # type: ignore
        result = await generate_and_send()
        logger.info("Daily email sent: %s", result)
    except ImportError:
        # generate_and_send가 없으면 subprocess로 CLI 호출
        import subprocess
        from pathlib import Path
        project_root = Path(__file__).resolve().parent.parent
        venv_python = project_root / "venv" / "Scripts" / "python.exe"
        try:
            proc = subprocess.run(
                [str(venv_python), "-m", "scripts.daily_email_report"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=600,
            )
            if proc.returncode == 0:
                logger.info("Daily email cron complete (CLI fallback)")
            else:
                logger.error("Daily email failed: %s", proc.stderr[-500:])
        except Exception as exc:
            logger.error("Daily email cron error: %s", exc)
    except Exception as exc:
        logger.error("Daily email failed: %s", exc)


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=ET_TZ)

    # 매월 1일 04:00 ET — Stage 1
    sched.add_job(
        run_stage1,
        CronTrigger(day="1", hour=4, minute=0, timezone=ET_TZ),
        id="stage1_monthly",
        name="Stage 1 Universe (monthly)",
        replace_existing=True,
    )

    # 매일 08:55 ET (월~금) — Stage 2
    sched.add_job(
        run_stage2,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=55, timezone=ET_TZ),
        id="stage2_daily",
        name="Stage 2 Daily Picks",
        replace_existing=True,
    )

    # 매일 16:30 ET (월~금) — outcome backfill
    sched.add_job(
        run_outcome_backfill,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=ET_TZ),
        id="outcome_backfill",
        name="Daily Picks Outcome Backfill",
        replace_existing=True,
    )

    # 매일 09:15 ET (월~금) — 데일리 이메일 리포트
    # 09:00이 프리마켓 데이터 최적 시점이지만, email 자체가 picks를 fetch하므로
    # 09:15에 발송하면 09:15 시점 데이터 사용 (정규장 09:30 직전, premarket 거의 확정)
    sched.add_job(
        run_daily_email,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=15, timezone=ET_TZ),
        id="daily_email",
        name="Daily Email Report",
        replace_existing=True,
    )

    # 매일 09:25 ET (월~금) — 3 시스템 picks 통합 비교 로그 (개장 직전)
    sched.add_job(
        run_comparison_log,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=25, timezone=ET_TZ),
        id="comparison_log",
        name="Comparison Daily Picks Log (3 systems)",
        replace_existing=True,
    )

    # 매일 16:35 ET (월~금) — 1d/5d/10d outcome 백필
    sched.add_job(
        run_comparison_backfill,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone=ET_TZ),
        id="comparison_backfill",
        name="Comparison Outcomes Backfill (1d/5d/10d)",
        replace_existing=True,
    )

    return sched


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sched = build_scheduler()
    sched.start()
    logger.info(
        "Scanner scheduler started. Next runs:\n  %s",
        "\n  ".join(f"{j.name}: {j.next_run_time}" for j in sched.get_jobs()),
    )
    # 프로세스 유지
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
