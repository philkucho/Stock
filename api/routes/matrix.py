"""매트릭스 백테스트 결과 조회 + 실행 트리거.

데이터 소스: data/matrix_runs.parquet (CLI `python -m backtests.run_matrix` 출력)
DB 동기화는 추후 — 현재는 parquet이 진실의 원천 (idempotency 키도 그쪽).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from backtests.data_cache import REPO_ROOT

router = APIRouter()

MATRIX_PARQUET = REPO_ROOT / "data" / "matrix_runs.parquet"


class MatrixCell(BaseModel):
    symbol: str
    preset_key: str
    period_start: str
    period_end: str
    fitness: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    total_return: float
    total_pnl: float
    total_fills: int
    total_positions: int
    wins: int
    losses: int
    cost_adj_return: float
    starting_cash: float
    final_equity: float
    params_hash: str


class MatrixResponse(BaseModel):
    cells: list[MatrixCell]
    symbols: list[str]
    presets: list[str]
    period_start: str | None
    period_end: str | None
    total: int


def _load_matrix_df() -> pd.DataFrame:
    if not MATRIX_PARQUET.exists():
        return pd.DataFrame()
    return pd.read_parquet(MATRIX_PARQUET)


@router.get("/", response_model=MatrixResponse)
async def get_matrix(
    period_start: str | None = Query(default=None, description="YYYY-MM-DD"),
    period_end: str | None = Query(default=None, description="YYYY-MM-DD"),
    symbol: str | None = Query(default=None, description="Filter by single symbol"),
    preset: str | None = Query(default=None, description="Filter by single preset"),
) -> MatrixResponse:
    df = _load_matrix_df()
    if df.empty:
        return MatrixResponse(
            cells=[], symbols=[], presets=[], period_start=None, period_end=None, total=0
        )

    if period_start:
        df = df[df["period_start"] == period_start]
    if period_end:
        df = df[df["period_end"] == period_end]
    if symbol:
        df = df[df["symbol"] == symbol.upper()]
    if preset:
        df = df[df["preset_key"] == preset]

    df = df.sort_values("fitness", ascending=False)

    cells: list[MatrixCell] = []
    for _, row in df.iterrows():
        cells.append(
            MatrixCell(
                symbol=str(row["symbol"]),
                preset_key=str(row["preset_key"]),
                period_start=str(row["period_start"]),
                period_end=str(row["period_end"]),
                fitness=float(row["fitness"]),
                sharpe=float(row["sharpe"]),
                max_drawdown=float(row["max_drawdown"]),
                win_rate=float(row["win_rate"]),
                total_return=float(row["total_return"]),
                total_pnl=float(row["total_pnl"]),
                total_fills=int(row["total_fills"]),
                total_positions=int(row["total_positions"]),
                wins=int(row["wins"]),
                losses=int(row["losses"]),
                cost_adj_return=float(row["cost_adj_return"]),
                starting_cash=float(row["starting_cash"]),
                final_equity=float(row["final_equity"]),
                params_hash=str(row["params_hash"]),
            )
        )

    return MatrixResponse(
        cells=cells,
        symbols=sorted(df["symbol"].unique().tolist()),
        presets=sorted(df["preset_key"].unique().tolist()),
        period_start=str(df["period_start"].iloc[0]) if not df.empty else None,
        period_end=str(df["period_end"].iloc[0]) if not df.empty else None,
        total=len(cells),
    )


class RunMatrixRequest(BaseModel):
    pool: str = "default"
    presets: str = "all"
    train: str = "2020-01-01:2022-12-31"
    test: str = "2023-01-01:2024-12-31"
    workers: int = 4
    cash: int = 100_000
    force: bool = False


class RunMatrixResponse(BaseModel):
    status: str
    pid: int | None = None
    message: str


def _spawn_matrix_run(req: RunMatrixRequest) -> int:
    """venv python으로 run_matrix.py 실행. PID 반환."""
    py = REPO_ROOT / "venv" / "Scripts" / "python.exe"
    args: list[str] = [
        str(py),
        "-m",
        "backtests.run_matrix",
        "--pool",
        req.pool,
        "--presets",
        req.presets,
        "--train",
        req.train,
        "--test",
        req.test,
        "--workers",
        str(req.workers),
        "--cash",
        str(req.cash),
    ]
    if req.force:
        args.append("--force")

    proc = subprocess.Popen(
        args,
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.pid


@router.post("/run", response_model=RunMatrixResponse)
async def run_matrix(req: RunMatrixRequest) -> RunMatrixResponse:
    """매트릭스 백테스트를 백그라운드 프로세스로 시작 (비동기, 즉시 반환)."""
    try:
        pid = await asyncio.to_thread(_spawn_matrix_run, req)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500, detail=f"Python venv not found: {exc}"
        ) from exc
    return RunMatrixResponse(
        status="started", pid=pid, message=f"Matrix run spawned (PID {pid}). Tail logs from CLI."
    )


@router.get("/periods")
async def list_periods() -> list[dict[str, Any]]:
    """저장된 (period_start, period_end) 조합 목록 — UI 셀렉터용."""
    df = _load_matrix_df()
    if df.empty:
        return []
    grouped = (
        df.groupby(["period_start", "period_end"])
        .size()
        .reset_index(name="count")
        .sort_values("period_start", ascending=False)
    )
    return [
        {
            "period_start": str(r["period_start"]),
            "period_end": str(r["period_end"]),
            "count": int(r["count"]),
        }
        for _, r in grouped.iterrows()
    ]
