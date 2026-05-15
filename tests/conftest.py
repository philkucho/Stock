"""Pytest 공통 설정 — import 시점 DB 엔진 생성을 위해 dummy DATABASE_URL 주입."""

from __future__ import annotations

import os

# 테스트는 실제 DB 연결을 안 하지만 api.db.session이 import 시점에
# create_async_engine을 호출하므로 형식상 유효한 URL이 필요.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
