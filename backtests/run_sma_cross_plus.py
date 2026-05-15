"""SmaCrossPlus 백테스트 실행 스크립트.

`run_sma_cross.py`와 동일한 파이프라인 (yfinance/DB → BacktestEngine → 결과 출력 + DB 저장)
을 SmaCrossPlus 전략에 대해 수행. RSI 필터, ATR 기반 손절/익절, 트레일링 스탑 토글 포함.

사용 예:
    python -m backtests.run_sma_cross_plus --symbol AAPL --from-db --save
    python -m backtests.run_sma_cross_plus --symbol AAPL --no-rsi --no-atr-stops --save
    python -m backtests.run_sma_cross_plus --trailing --stop-mult 1.5 --target-mult 3 --save
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal

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

from backtests._db_helpers import save_backtest_run
from backtests.run_sma_cross import fetch_bars  # 공통 데이터 로더 재사용
from strategies.sma_cross_plus import SmaCrossPlus, SmaCrossPlusConfig


def run_backtest(
    symbol: str,
    start: str,
    end: str,
    fast_period: int,
    slow_period: int,
    trade_size: Decimal,
    starting_cash: int,
    *,
    use_rsi_filter: bool,
    rsi_period: int,
    rsi_overbought: float,
    use_atr_stops: bool,
    atr_period: int,
    stop_atr_mult: float,
    target_atr_mult: float,
    use_trailing_stop: bool,
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
    print(f"  Loaded {len(df)} rows. First={df.index[0].date()} Last={df.index[-1].date()}\n")

    wrangler = BarDataWrangler(bar_type, instrument)
    bars = wrangler.process(df)

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

    strategy = SmaCrossPlus(
        config=SmaCrossPlusConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_period=fast_period,
            slow_period=slow_period,
            use_rsi_filter=use_rsi_filter,
            rsi_period=rsi_period,
            rsi_overbought=rsi_overbought,
            use_atr_stops=use_atr_stops,
            atr_period=atr_period,
            stop_atr_mult=stop_atr_mult,
            target_atr_mult=target_atr_mult,
            use_trailing_stop=use_trailing_stop,
        )
    )
    engine.add_strategy(strategy)

    filters_str = []
    if use_rsi_filter:
        filters_str.append(f"RSI<{rsi_overbought:.0f}")
    if use_atr_stops:
        trailing = " (trailing)" if use_trailing_stop else ""
        filters_str.append(f"ATR-stop {stop_atr_mult:.1f}x / target {target_atr_mult:.1f}x{trailing}")
    print(
        f"Running: SMA({fast_period}, {slow_period}) on {symbol}, qty={trade_size}, "
        f"cash=${starting_cash:,} | {' + '.join(filters_str) or 'no filters'}"
    )
    engine.run()

    print("\n" + "=" * 70)
    print(f"RESULTS — SmaCrossPlus | {symbol} | {start} ~ {end}")
    print("=" * 70)

    account_report = engine.trader.generate_account_report(venue)
    fills = engine.trader.generate_order_fills_report()
    positions = engine.trader.generate_positions_report()

    print(f"\nFills: {len(fills)} total, Positions: {len(positions)} closed")

    total_pnl = 0.0
    wins = losses = 0
    win_rate = 0.0
    pnl_values: list[float] = []

    if not positions.empty and "realized_pnl" in positions.columns:
        pnl_series = positions["realized_pnl"].astype(str).str.split().str[0].astype(float)
        pnl_values = pnl_series.tolist()
        total_pnl = float(pnl_series.sum())
        wins = int((pnl_series > 0).sum())
        losses = int((pnl_series < 0).sum())
        total = wins + losses
        win_rate = wins / total * 100 if total else 0.0
        print(f"Summary: {wins}W / {losses}L  win_rate={win_rate:.1f}%  total_pnl=${total_pnl:,.2f}")

    final_equity = float(starting_cash) + total_pnl
    if not account_report.empty and "total" in account_report.columns:
        try:
            final_equity = float(account_report["total"].iloc[-1])
        except Exception:
            pass

    if save:
        record = {
            "strategy_name": "SmaCrossPlus",
            "strategy_params": {
                "fast_period": fast_period,
                "slow_period": slow_period,
                "trade_size": str(trade_size),
                "use_rsi_filter": use_rsi_filter,
                "rsi_period": rsi_period,
                "rsi_overbought": rsi_overbought,
                "use_atr_stops": use_atr_stops,
                "atr_period": atr_period,
                "stop_atr_mult": stop_atr_mult,
                "target_atr_mult": target_atr_mult,
                "use_trailing_stop": use_trailing_stop,
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
            "win_rate": Decimal(f"{win_rate / 100:.4f}"),
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
    p = argparse.ArgumentParser(description="SmaCrossPlus backtest (RSI filter + ATR stops)")
    p.add_argument("--symbol", default="AAPL")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--fast", type=int, default=10)
    p.add_argument("--slow", type=int, default=30)
    p.add_argument("--qty", type=str, default="10")
    p.add_argument("--cash", type=int, default=100_000)

    # RSI
    p.add_argument("--no-rsi", action="store_true", help="Disable RSI filter")
    p.add_argument("--rsi-period", type=int, default=14)
    p.add_argument("--rsi-overbought", type=float, default=70.0)

    # ATR stops
    p.add_argument("--no-atr-stops", action="store_true", help="Disable ATR stops/targets")
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--stop-mult", type=float, default=2.0, help="Stop = entry - atr*M")
    p.add_argument("--target-mult", type=float, default=4.0, help="Target = entry + atr*M")
    p.add_argument("--trailing", action="store_true", help="Enable trailing stop")

    # I/O
    p.add_argument("--from-db", action="store_true")
    p.add_argument("--save", action="store_true")
    p.add_argument("--notes", type=str, default=None)
    args = p.parse_args()

    return run_backtest(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        fast_period=args.fast,
        slow_period=args.slow,
        trade_size=Decimal(args.qty),
        starting_cash=args.cash,
        use_rsi_filter=not args.no_rsi,
        rsi_period=args.rsi_period,
        rsi_overbought=args.rsi_overbought,
        use_atr_stops=not args.no_atr_stops,
        atr_period=args.atr_period,
        stop_atr_mult=args.stop_mult,
        target_atr_mult=args.target_mult,
        use_trailing_stop=args.trailing,
        from_db=args.from_db,
        save=args.save,
        notes=args.notes,
    )


if __name__ == "__main__":
    sys.exit(main())
