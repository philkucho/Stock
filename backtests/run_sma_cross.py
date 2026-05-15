"""SMA Crossover 백테스트 실행 스크립트.

기본은 yfinance에서 직접 다운로드. `--from-db` 플래그를 주면 TimescaleDB의
bars 테이블에서 읽음 (사전에 `python -m scripts.ingest_bars`로 적재 필요).

사용 예:
    venv/Scripts/python.exe -m backtests.run_sma_cross
    venv/Scripts/python.exe -m backtests.run_sma_cross --symbol MSFT --start 2020-01-01
    venv/Scripts/python.exe -m backtests.run_sma_cross --fast 5 --slow 20
    venv/Scripts/python.exe -m backtests.run_sma_cross --from-db --symbol AAPL --start 2022-01-01
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from backtests._db_helpers import save_backtest_run
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarSpecification, BarType
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    BarAggregation,
    OmsType,
    PriceType,
)
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategies.sma_cross import SmaCross, SmaCrossConfig


def fetch_bars_yfinance(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    """yfinance에서 OHLCV를 받아 NautilusTrader BarDataWrangler 입력 포맷으로 변환."""
    df = yf.download(
        symbol,
        start=start,
        end=end,
        interval=interval,
        progress=False,
        auto_adjust=False,
    )
    if df is None or df.empty:
        raise ValueError(f"yfinance returned no data for {symbol} ({start}..{end})")

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


def fetch_bars_db(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    """TimescaleDB bars hypertable에서 OHLCV 조회. yfinance 출력과 동일 포맷 반환."""
    load_dotenv()
    sync_url = os.environ.get("DATABASE_URL_SYNC")
    if not sync_url:
        async_url = os.environ.get("DATABASE_URL", "")
        sync_url = async_url.replace("+asyncpg", "+psycopg")
    if not sync_url:
        raise RuntimeError("DATABASE_URL_SYNC or DATABASE_URL must be set in .env")

    engine = create_engine(sync_url)
    query = text(
        """
        SELECT time, open, high, low, close, volume
        FROM bars
        WHERE symbol = :symbol
          AND interval = :interval
          AND time >= :start
          AND time < :end
        ORDER BY time
        """
    )
    df = pd.read_sql(
        query,
        engine,
        params={"symbol": symbol, "interval": interval, "start": start, "end": end},
    )
    if df.empty:
        raise ValueError(
            f"No bars in DB for {symbol} {interval} {start}..{end}. "
            f"Run: python -m scripts.ingest_bars --symbol {symbol} --start {start} --end {end} --interval {interval}"
        )
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
    # Numeric → float (NautilusTrader BarDataWrangler는 float 입력 가정)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def fetch_bars(symbol: str, start: str, end: str, interval: str = "1d", *, from_db: bool = False) -> pd.DataFrame:
    """from_db=True면 DB, 아니면 yfinance에서 조회."""
    if from_db:
        return fetch_bars_db(symbol, start, end, interval)
    return fetch_bars_yfinance(symbol, start, end, interval)


def run_backtest(
    symbol: str,
    start: str,
    end: str,
    fast_period: int,
    slow_period: int,
    trade_size: Decimal,
    starting_cash: int,
    from_db: bool = False,
    save: bool = False,
    notes: str | None = None,
) -> int:
    venue = Venue("XNAS")
    instrument = TestInstrumentProvider.equity(symbol=symbol, venue="XNAS")
    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(
            step=1,
            aggregation=BarAggregation.DAY,
            price_type=PriceType.LAST,
        ),
        aggregation_source=AggregationSource.EXTERNAL,
    )

    source = "DB(bars)" if from_db else "yfinance"
    print(f"Fetching {symbol} {start} ~ {end} (1d) from {source}...")
    df = fetch_bars(symbol, start, end, from_db=from_db)
    print(f"  Loaded {len(df)} rows. First={df.index[0].date()} Last={df.index[-1].date()}")

    wrangler = BarDataWrangler(bar_type, instrument)
    bars = wrangler.process(df)
    print(f"  Wrangled into {len(bars)} NautilusTrader bars\n")

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="BACKTESTER-001",
            logging=LoggingConfig(log_level="WARN"),
        )
    )
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=USD,
        starting_balances=[Money(starting_cash, USD)],
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    strategy = SmaCross(
        config=SmaCrossConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_period=fast_period,
            slow_period=slow_period,
        )
    )
    engine.add_strategy(strategy)

    print(f"Running backtest: SMA({fast_period}) x SMA({slow_period}) on {symbol}, qty={trade_size}, cash=${starting_cash:,}")
    engine.run()

    print("\n" + "=" * 70)
    print(f"RESULTS — {symbol} | SMA({fast_period}, {slow_period}) | {start} ~ {end}")
    print("=" * 70)

    account_report = engine.trader.generate_account_report(venue)
    print("\nAccount:")
    print(account_report.tail(3) if not account_report.empty else "(empty)")

    fills = engine.trader.generate_order_fills_report()
    print(f"\nFills: {len(fills)} total")
    if not fills.empty:
        cols = [c for c in ["ts_init", "instrument_id", "order_side", "last_qty", "last_px"] if c in fills.columns]
        print(fills[cols].head(10).to_string())
        if len(fills) > 10:
            print(f"... +{len(fills) - 10} more")

    positions = engine.trader.generate_positions_report()
    print(f"\nPositions: {len(positions)} closed")

    # 기본 메트릭 계산
    total_pnl = 0.0
    wins = 0
    losses = 0
    win_rate = 0.0
    pnl_values: list[float] = []

    if not positions.empty:
        cols = [c for c in ["instrument_id", "side", "quantity", "realized_pnl", "realized_return"] if c in positions.columns]
        print(positions[cols].to_string())

        if "realized_pnl" in positions.columns:
            # realized_pnl 컬럼은 "-88.10 USD" 문자열 형식이므로 숫자 부분만 추출
            pnl_series = positions["realized_pnl"].astype(str).str.split().str[0].astype(float)
            pnl_values = pnl_series.tolist()
            total_pnl = float(pnl_series.sum())
            wins = int((pnl_series > 0).sum())
            losses = int((pnl_series < 0).sum())
            total = wins + losses
            win_rate = wins / total * 100 if total else 0.0
            print(f"\nSummary: {wins}W / {losses}L  win_rate={win_rate:.1f}%  total_pnl=${total_pnl:,.2f}")

    # final_equity: account report의 마지막 total
    final_equity = float(starting_cash) + total_pnl
    if not account_report.empty and "total" in account_report.columns:
        try:
            final_equity = float(account_report["total"].iloc[-1])
        except Exception:
            pass

    if save:
        record = {
            "strategy_name": "SmaCross",
            "strategy_params": {
                "fast_period": fast_period,
                "slow_period": slow_period,
                "trade_size": str(trade_size),
            },
            "symbol": symbol,
            "interval": "1d",
            "period_start": datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
            "period_end": datetime.fromisoformat(end).replace(tzinfo=timezone.utc),
            "data_source": "db" if from_db else "yfinance",
            "starting_cash": Decimal(str(starting_cash)),
            "final_equity": Decimal(f"{final_equity:.2f}"),
            "total_pnl": Decimal(f"{total_pnl:.2f}"),
            "total_fills": int(len(fills)),
            "total_positions": int(len(positions)),
            "wins": wins,
            "losses": losses,
            "win_rate": Decimal(f"{win_rate / 100:.4f}"),  # 0~1
            "metrics": {
                "best_position_pnl": float(max(pnl_values)) if pnl_values else 0.0,
                "worst_position_pnl": float(min(pnl_values)) if pnl_values else 0.0,
                "avg_position_pnl": float(sum(pnl_values) / len(pnl_values)) if pnl_values else 0.0,
            },
            "notes": notes,
        }
        run_id = save_backtest_run(record)
        print(f"\n💾 Saved as backtest_runs.id={run_id}")

    engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SMA Crossover backtest using yfinance + NautilusTrader")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--fast", type=int, default=10, help="Fast SMA period (bars)")
    parser.add_argument("--slow", type=int, default=30, help="Slow SMA period (bars)")
    parser.add_argument("--qty", type=str, default="10", help="Trade size (shares)")
    parser.add_argument("--cash", type=int, default=100_000, help="Starting cash USD")
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Read bars from TimescaleDB instead of yfinance (run scripts.ingest_bars first)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save backtest result into backtest_runs table",
    )
    parser.add_argument("--notes", type=str, default=None, help="Free-form notes for this run")
    args = parser.parse_args()

    return run_backtest(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        fast_period=args.fast,
        slow_period=args.slow,
        trade_size=Decimal(args.qty),
        starting_cash=args.cash,
        from_db=args.from_db,
        save=args.save,
        notes=args.notes,
    )


if __name__ == "__main__":
    sys.exit(main())
