"""FastAPI 진입점.

NautilusTrader 노드와의 통합은 deps.py에서 lifespan으로 관리.
대시보드(Next.js)에서 사용할 REST + WebSocket 엔드포인트를 routes/에서 마운트.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import engine, get_session
from api.routes import (
    advisor,
    assignments,
    backtests,
    comparison,
    dashboard,
    market_diagnosis,
    matrix,
    picks,
    positions,
    regime,
    review,
    scanner,
    signals,
    strategies,
    telegram,
    trading,
    ws,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # TODO Day 7+: NautilusTrader TradingNode 시작/종료를 여기서 관리
    # 백그라운드: trading 캐시 30분마다 자동 워밍.
    # _build_market_brief / _build_picks 내부의 동기 yfinance 호출은
    # asyncio.to_thread로 감싸져 있어 이벤트 루프를 막지 않음.
    warm_task = asyncio.create_task(trading.warm_trading_cache_loop())
    try:
        yield
    finally:
        warm_task.cancel()
        try:
            await warm_task
        except asyncio.CancelledError:
            pass
        await engine.dispose()


app = FastAPI(
    title="Stock Autotrader API",
    version="0.1.1",
    lifespan=lifespan,
)


# FastAPI redirect_slashes=True (default)가 trailing slash 정규화 시 Location header를
# 절대 URL (http://127.0.0.1:8000/...)로 보낸다. mobile에서 그걸 follow하면
# mobile 자신의 127.0.0.1 = FastAPI 없음 → fail.
# 해결: redirect 응답의 Location을 path-only (상대)로 재작성.
@app.middleware("http")
async def relativize_redirect_location(request, call_next):
    response = await call_next(request)
    if 300 <= response.status_code < 400:
        loc = response.headers.get("location")
        if loc and (loc.startswith("http://") or loc.startswith("https://")):
            # 절대 URL → path-only. 같은 origin이면 host 제거 OK.
            try:
                from urllib.parse import urlparse
                parsed = urlparse(loc)
                relative = parsed.path
                if parsed.query:
                    relative += "?" + parsed.query
                response.headers["location"] = relative
            except Exception:
                pass
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    # Tailscale (100.64.0.0/10), 사설망 (192.168.x.x, 10.x.x.x) origin 허용.
    # 폰에서 100.x.y.z:3000으로 접속하면 origin도 그 형태가 됨.
    allow_origin_regex=r"http://(100\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+):3000",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # pragma: no cover
        db_status = f"error: {exc.__class__.__name__}"
    return {"status": "ok", "version": "0.1.0", "db": db_status}


app.include_router(signals.router, prefix="/api/signals", tags=["signals"])
app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
app.include_router(matrix.router, prefix="/api/matrix", tags=["matrix"])
app.include_router(regime.router, prefix="/api/regime", tags=["regime"])
app.include_router(assignments.router, prefix="/api/assignments", tags=["assignments"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(backtests.router, prefix="/api/backtests", tags=["backtests"])
app.include_router(picks.router, prefix="/api/picks", tags=["picks"])
app.include_router(scanner.router, prefix="/api/scanner", tags=["scanner"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(comparison.router, prefix="/api/comparison", tags=["comparison"])
app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
app.include_router(review.router, prefix="/api/review", tags=["review"])
app.include_router(advisor.router, prefix="/api/advisor", tags=["advisor"])
app.include_router(telegram.router, prefix="/api/telegram", tags=["telegram"])
app.include_router(
    market_diagnosis.router,
    prefix="/api/market-diagnosis",
    tags=["market-diagnosis"],
)
app.include_router(ws.router, prefix="/ws", tags=["ws"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", "8000")),
        reload=True,
    )
