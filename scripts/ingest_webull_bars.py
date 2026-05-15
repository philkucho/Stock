"""Webull 1m bar 적재 스크립트.

Webull MarketData API는 한 번에 max 1650 bars만 반환하고 시간 windowing 파라미터를
지원하지 않음. 따라서 매 호출마다 "현재 시각 기준 가장 최근 N개"만 받음.
RTH only 기준 1650 / 390 ≈ 4.2 거래일치 데이터.

매일 cron으로 실행하면 신규 봉만 누적되어 60일+ 히스토리 자연 축적.
중복은 (time, symbol, interval) PK 기준 ON CONFLICT DO NOTHING.

사용 예:
    venv/Scripts/python.exe -m scripts.ingest_webull_bars
    venv/Scripts/python.exe -m scripts.ingest_webull_bars --symbols AAPL,MSFT
    venv/Scripts/python.exe -m scripts.ingest_webull_bars --whitelist-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from api.db import async_session_factory  # noqa: E402
from api.db.models import Bar  # noqa: E402
from webull_adapter.config import WebullCredentials  # noqa: E402
from webullsdkcore.client import ApiClient  # noqa: E402
from webullsdkcore.exception.exceptions import ServerException  # noqa: E402
from webullsdkmdata.common.category import Category  # noqa: E402
from webullsdkmdata.common.timespan import Timespan  # noqa: E402
from webullsdkmdata.quotes.market_data import MarketData  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILTER_PATH = PROJECT_ROOT / "data" / "symbol_filter.json"
WEBULL_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"
MAX_COUNT = 1650
SLEEP_BETWEEN_SYMBOLS_SEC = 1.0


def load_whitelist_symbols(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return sorted(r["symbol"] for r in payload.get("whitelist", []))


def fetch_webull_bars(md: MarketData, symbol: str) -> list[dict]:
    response = md.get_history_bar(
        symbol,
        Category.US_STOCK,
        Timespan.M1,
        count=str(MAX_COUNT),
        trading_sessions=["RTH"],
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text!r}")
    return response.json()


def rows_to_db(rows: list[dict], symbol: str) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        ts = datetime.strptime(r["time"], WEBULL_TIME_FORMAT)
        out.append({
            "time": ts,
            "symbol": symbol,
            "interval": "1m",
            "open": Decimal(r["open"]),
            "high": Decimal(r["high"]),
            "low": Decimal(r["low"]),
            "close": Decimal(r["close"]),
            "volume": Decimal(r["volume"]),
            "source": "webull",
        })
    return out


async def upsert_bars(rows: list[dict]) -> int:
    if not rows:
        return 0
    async with async_session_factory() as session:
        stmt = pg_insert(Bar).values(rows).on_conflict_do_nothing(
            index_elements=["time", "symbol", "interval"]
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def ingest_one(md: MarketData, symbol: str) -> tuple[int, int, str | None]:
    """단일 종목 적재. (다운로드 행 수, 신규 삽입, 가장 오래된 ts) 반환."""
    print(f"  Fetching {symbol} 1m × {MAX_COUNT} (RTH)...", flush=True)
    raw = fetch_webull_bars(md, symbol)
    rows = rows_to_db(raw, symbol)
    inserted = await upsert_bars(rows)
    oldest = raw[-1]["time"] if raw else None
    newest = raw[0]["time"] if raw else None
    print(
        f"  {symbol}: {len(rows)} downloaded, {inserted} inserted "
        f"({len(rows) - inserted} duplicate)  range={oldest} ~ {newest}",
        flush=True,
    )
    return len(rows), inserted, oldest


async def main_async(symbols: list[str]) -> int:
    creds = WebullCredentials.from_env()
    client = ApiClient(app_key=creds.app_key, app_secret=creds.app_secret, region_id=creds.region)
    md = MarketData(client)

    started = datetime.now(timezone.utc)
    print(f"Ingesting Webull 1m bars for {len(symbols)} symbols (RTH only): {', '.join(symbols)}\n")

    total_dl, total_ins = 0, 0
    failures: list[tuple[str, str]] = []

    for sym in symbols:
        try:
            d, i, _ = await ingest_one(md, sym)
            total_dl += d
            total_ins += i
        except (ServerException, RuntimeError) as exc:
            print(f"  {sym}: FAILED — {type(exc).__name__}: {exc}", flush=True)
            failures.append((sym, str(exc)))
        time.sleep(SLEEP_BETWEEN_SYMBOLS_SEC)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(
        f"\nDone in {elapsed:.1f}s. "
        f"Total: {total_dl} downloaded, {total_ins} inserted, {len(failures)} failed."
    )
    return 0 if not failures else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbols", help="Comma-separated tickers (override whitelist)")
    g.add_argument("--whitelist-only", action="store_true", default=True,
                   help="data/symbol_filter.json의 whitelist만 적재 (default)")
    p.add_argument("--filter", type=Path, default=DEFAULT_FILTER_PATH,
                   help="symbol_filter.json 경로")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = load_whitelist_symbols(args.filter)
    if not symbols:
        print("No symbols to ingest.", file=sys.stderr)
        return 1
    return asyncio.run(main_async(symbols))


if __name__ == "__main__":
    sys.exit(main())
