"""yfinance → bars hypertable 적재 스크립트.

yfinance에서 OHLCV 데이터를 받아 TimescaleDB의 bars 테이블에 적재.
중복(time, symbol, interval) 시 무시 (ON CONFLICT DO NOTHING).

사용 예:
    venv/Scripts/python.exe -m scripts.ingest_bars --symbol AAPL --start 2020-01-01
    venv/Scripts/python.exe -m scripts.ingest_bars --symbol MSFT --interval 1h --start 2024-01-01
    venv/Scripts/python.exe -m scripts.ingest_bars --symbols AAPL,MSFT,GOOGL --start 2023-01-01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert as pg_insert

load_dotenv()  # api.db.session import 전에 .env 로드

from api.db import async_session_factory  # noqa: E402
from api.db.models import Bar  # noqa: E402


def fetch_yfinance_bars(symbol: str, start: str, end: str | None, interval: str) -> pd.DataFrame:
    """yfinance에서 OHLCV 다운로드, UTC 정규화 후 반환."""
    df = yf.download(
        symbol,
        start=start,
        end=end,
        interval=interval,
        progress=False,
        auto_adjust=False,
    )
    if df is None or df.empty:
        raise ValueError(f"yfinance returned no data for {symbol} ({start}..{end}, {interval})")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns=str.lower)
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df = df.dropna(subset=["open", "high", "low", "close"])

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    return df


def df_to_bar_rows(df: pd.DataFrame, symbol: str, interval: str, source: str) -> list[dict]:
    """DataFrame → bars insert용 dict 리스트."""
    rows: list[dict] = []
    for ts, row in df.iterrows():
        rows.append(
            {
                "time": ts.to_pydatetime(),
                "symbol": symbol,
                "interval": interval,
                "open": Decimal(str(row["open"])),
                "high": Decimal(str(row["high"])),
                "low": Decimal(str(row["low"])),
                "close": Decimal(str(row["close"])),
                "volume": Decimal(str(row["volume"])),
                "source": source,
            }
        )
    return rows


async def upsert_bars(rows: list[dict]) -> int:
    """bars 테이블에 ON CONFLICT DO NOTHING으로 적재. 신규 삽입 행 수 반환."""
    if not rows:
        return 0

    async with async_session_factory() as session:
        stmt = pg_insert(Bar).values(rows).on_conflict_do_nothing(
            index_elements=["time", "symbol", "interval"]
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def ingest_one(symbol: str, start: str, end: str | None, interval: str) -> tuple[int, int]:
    """단일 종목 적재. (다운로드 행 수, 신규 삽입 행 수) 반환."""
    print(f"  Fetching {symbol} {start} ~ {end or 'now'} ({interval})...", flush=True)
    df = fetch_yfinance_bars(symbol, start, end, interval)
    rows = df_to_bar_rows(df, symbol, interval, source="yfinance")
    inserted = await upsert_bars(rows)
    print(
        f"  {symbol}: {len(rows)} downloaded, {inserted} inserted "
        f"({len(rows) - inserted} duplicate)",
        flush=True,
    )
    return len(rows), inserted


async def main_async(symbols: list[str], start: str, end: str | None, interval: str) -> int:
    started = datetime.now(timezone.utc)
    print(f"Ingesting {len(symbols)} symbol(s): {', '.join(symbols)}\n")

    total_downloaded = 0
    total_inserted = 0
    failures: list[tuple[str, str]] = []

    for symbol in symbols:
        try:
            d, i = await ingest_one(symbol, start, end, interval)
            total_downloaded += d
            total_inserted += i
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol}: FAILED — {type(exc).__name__}: {exc}", flush=True)
            failures.append((symbol, str(exc)))

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(
        f"\nDone in {elapsed:.1f}s. "
        f"Total: {total_downloaded} downloaded, {total_inserted} inserted, {len(failures)} failed."
    )
    if failures:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ingest yfinance OHLCV into TimescaleDB bars hypertable"
    )
    g = p.add_mutually_exclusive_group(required=False)
    g.add_argument("--symbol", help="Single ticker (e.g. AAPL)")
    g.add_argument("--symbols", help="Comma-separated tickers (e.g. AAPL,MSFT,GOOGL)")
    g.add_argument("--from-file", help="File path with tickers (newline OR comma separated)")
    p.add_argument("--start", default="2020-01-01", help="Start date (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="End date (YYYY-MM-DD), default = now")
    p.add_argument(
        "--interval",
        default="1d",
        choices=["1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo"],
        help="Bar interval (yfinance supported values)",
    )
    args = p.parse_args()
    if not args.symbol and not args.symbols and not args.from_file:
        args.symbol = "AAPL"
    return args


def main() -> int:
    args = parse_args()
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.from_file:
        from pathlib import Path  # noqa: PLC0415
        text = Path(args.from_file).read_text(encoding="utf-8")
        # support both newline and comma separators
        raw = text.replace(",", "\n").splitlines()
        symbols = sorted({s.strip().upper() for s in raw if s.strip()})
    else:
        symbols = [args.symbol.upper()]

    return asyncio.run(main_async(symbols, args.start, args.end, args.interval))


if __name__ == "__main__":
    sys.exit(main())
