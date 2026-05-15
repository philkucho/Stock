from __future__ import annotations

import os
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env.")
    return url


# pool_size 5 + max_overflow 10 (default 15)으로는 backfill 병렬 30개 처리 시 부족.
# 50까지 허용 (FastAPI 일반 트래픽 + backfill cron 동시 가동 여유).
engine = create_async_engine(
    _database_url(),
    pool_pre_ping=True,
    future=True,
    pool_size=20,
    max_overflow=30,
    pool_recycle=3600,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
