"""백테스트 결과 DB 저장/조회 헬퍼 (sync).

asyncio.run을 반복 호출하면 module-level async engine의 connection이 죽은 loop에 남아
'NoneType has no attribute send' 같은 에러 발생. 그리드 서치 같이 매 백테스트마다
DB 호출하는 시나리오에서는 sync engine + 매 호출 fresh session이 안전.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

load_dotenv()


def _sync_url() -> str:
    url = os.environ.get("DATABASE_URL_SYNC")
    if url:
        return url
    async_url = os.environ.get("DATABASE_URL", "")
    if async_url:
        return async_url.replace("+asyncpg", "+psycopg")
    raise RuntimeError("DATABASE_URL_SYNC or DATABASE_URL must be set in .env")


_engine = None


def _engine_singleton():
    """Lazy singleton sync engine. 같은 프로세스에서 재사용해도 안전 (sync, no event loop)."""
    global _engine
    if _engine is None:
        _engine = create_engine(_sync_url(), pool_pre_ping=True, future=True)
    return _engine


def save_backtest_run(record: dict[str, Any]) -> int:
    """백테스트 결과 1건 INSERT. id 반환."""
    from api.db.models import BacktestRun

    with Session(_engine_singleton()) as session:
        run = BacktestRun(**record)
        session.add(run)
        session.commit()
        session.refresh(run)
        return int(run.id)


def fetch_top_runs(
    symbol: str,
    start: str,
    end: str,
    *,
    strategy_name: str = "SmaCrossPlus",
    limit: int = 10,
) -> list[dict]:
    """동일 symbol/period의 결과를 PnL 내림차순으로 조회 (params/metrics 포함)."""
    from api.db.models import BacktestRun

    period_start = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    period_end = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    with Session(_engine_singleton()) as session:
        stmt = (
            select(BacktestRun)
            .where(
                BacktestRun.symbol == symbol,
                BacktestRun.strategy_name == strategy_name,
                BacktestRun.period_start == period_start,
                BacktestRun.period_end == period_end,
            )
            .order_by(BacktestRun.total_pnl.desc())
            .limit(limit)
        )
        rows = session.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "params": dict(r.strategy_params),
                "pnl": float(r.total_pnl),
                "win_rate": float(r.win_rate),
                "trades": r.total_positions,
                "notes": r.notes,
            }
            for r in rows
        ]
