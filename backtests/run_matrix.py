"""Strategy×Symbol fitness 매트릭스 백테스트 러너.

CLI:
    # 단일 셀 디버그
    python -m backtests.run_matrix --symbol AAPL --preset bnf_style --inspect

    # default 풀(50개) × 모든 프리셋
    python -m backtests.run_matrix --pool default --presets all

    # S&P 500 전체
    python -m backtests.run_matrix --pool sp500 --presets all --workers 8

결과: data/matrix_runs.parquet (append + dedup by params_hash).
재실행 시 동일 hash 셀은 스킵 (--force 로 강제).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# 워커가 stderr 버퍼를 채워 hang하는 것 방지: pandas FutureWarning 등 무시
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

import pandas as pd

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

from backtests.data_cache import REPO_ROOT, get_bars, get_pool
from backtests.walk_forward import WalkForwardSpec, parse_period_arg
from strategies import (
    PRESETS,
    CompositeStrategy,
    CompositeStrategyConfig,
    get_preset,
)

MATRIX_PARQUET = REPO_ROOT / "data" / "matrix_runs.parquet"


def _params_hash(symbol: str, preset_key: str, ws: WalkForwardSpec, preset: dict) -> str:
    payload = json.dumps(
        {
            "symbol": symbol,
            "preset": preset_key,
            "test_start": ws.test_start,
            "test_end": ws.test_end,
            "active_signals": list(preset["active_signals"]),
            "signal_weights": preset.get("signal_weights", {}),
            "buy_threshold": preset["buy_threshold"],
            "sell_threshold": preset["sell_threshold"],
            "stop_loss_pct": preset["stop_loss_pct"],
            "take_profit_pct": preset["take_profit_pct"],
            "max_hold_bars": preset.get("max_hold_bars"),
            "position_size_pct": preset.get("position_size_pct", 0.10),
        },
        sort_keys=True,
    )
    return hashlib.md5(payload.encode()).hexdigest()


def compute_metrics(
    account: pd.DataFrame, fills: pd.DataFrame, positions: pd.DataFrame, starting_cash: float
) -> dict:
    """Sharpe, MDD, win_rate, fitness 등 산출."""
    eq: pd.Series | None = None
    if account is not None and not account.empty:
        for col in ("total", "balance_total", "balance"):
            if col in account.columns:
                eq = pd.to_numeric(
                    account[col].astype(str).str.split().str[0], errors="coerce"
                ).dropna()
                break

    if eq is None or len(eq) == 0:
        if (
            positions is not None
            and not positions.empty
            and "realized_pnl" in positions.columns
        ):
            pnl_total = (
                positions["realized_pnl"]
                .astype(str)
                .str.split()
                .str[0]
                .astype(float)
                .sum()
            )
            eq = pd.Series([starting_cash, starting_cash + pnl_total])
        else:
            eq = pd.Series([starting_cash])

    final_equity = float(eq.iloc[-1])
    total_pnl = final_equity - starting_cash
    total_return = total_pnl / starting_cash

    if len(eq) > 1:
        rets = eq.pct_change().dropna()
        sharpe = (
            float(rets.mean() / rets.std() * (252**0.5)) if rets.std() and rets.std() > 0 else 0.0
        )
    else:
        sharpe = 0.0

    cummax = eq.cummax().replace(0, pd.NA)
    dd = (eq - cummax) / cummax
    max_drawdown = float(-dd.min()) if len(dd) and not dd.isna().all() else 0.0

    if (
        positions is None
        or positions.empty
        or "realized_pnl" not in positions.columns
    ):
        wins, losses, total_positions, win_rate = 0, 0, 0, 0.0
    else:
        pnl_series = (
            positions["realized_pnl"].astype(str).str.split().str[0].astype(float)
        )
        wins = int((pnl_series > 0).sum())
        losses = int((pnl_series < 0).sum())
        total_positions = wins + losses
        win_rate = wins / total_positions if total_positions else 0.0

    total_fills = len(fills) if fills is not None else 0
    cost_adj_return = total_return - 0.001 * total_fills  # 거래당 0.1% 차감

    car_norm = max(min(cost_adj_return, 1.0), -0.5)
    car_norm = (1.0 + car_norm) / 2.0  # [0.25, 1.0]
    fitness = sharpe * max(0.0, 1.0 - max_drawdown) * car_norm

    return {
        "starting_cash": float(starting_cash),
        "final_equity": final_equity,
        "total_pnl": total_pnl,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "total_fills": total_fills,
        "total_positions": total_positions,
        "wins": wins,
        "losses": losses,
        "cost_adj_return": cost_adj_return,
        "fitness": fitness,
    }


def run_single_cell(
    symbol: str,
    preset_key: str,
    ws: WalkForwardSpec,
    starting_cash: float = 100_000,
    verbose: bool = False,
) -> dict:
    """단일 (종목, 프리셋) 백테스트. 메트릭 + 메타 dict 반환."""
    instrument = TestInstrumentProvider.equity(symbol=symbol, venue="XNAS")
    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(
            step=1, aggregation=BarAggregation.DAY, price_type=PriceType.LAST
        ),
        aggregation_source=AggregationSource.EXTERNAL,
    )

    df = get_bars(symbol, ws.warmup_start, ws.test_end)
    if len(df) < 200:
        raise ValueError(f"{symbol}: insufficient bars ({len(df)})")

    wrangler = BarDataWrangler(bar_type, instrument)
    bars_full = wrangler.process(df)

    venue = Venue("XNAS")
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="MATRIX-001",
            logging=LoggingConfig(log_level="INFO" if verbose else "ERROR"),
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
    engine.add_data(bars_full)

    preset = get_preset(preset_key)
    cfg_kwargs = dict(
        instrument_id=instrument.id,
        bar_type=bar_type,
        active_signals=tuple(preset["active_signals"]),
        signal_weights=dict(preset.get("signal_weights", {})),
        buy_threshold=preset["buy_threshold"],
        sell_threshold=preset["sell_threshold"],
        stop_loss_pct=preset["stop_loss_pct"],
        take_profit_pct=preset["take_profit_pct"],
        starting_cash=float(starting_cash),
        position_size_pct=preset.get("position_size_pct", 0.10),
    )
    if preset.get("max_hold_bars") is not None:
        cfg_kwargs["max_hold_bars"] = preset["max_hold_bars"]
    strategy = CompositeStrategy(config=CompositeStrategyConfig(**cfg_kwargs))
    engine.add_strategy(strategy)
    engine.run()

    account = engine.trader.generate_account_report(venue)
    fills = engine.trader.generate_order_fills_report()
    positions = engine.trader.generate_positions_report()

    if verbose:
        print(f"\nAccount report ({len(account)} rows). Columns: {list(account.columns)}")
        if not account.empty:
            print(account.tail(3).to_string())
        print(f"\nFills: {len(fills)}")
        if not fills.empty:
            cols = [
                c
                for c in ["ts_init", "instrument_id", "order_side", "last_qty", "last_px"]
                if c in fills.columns
            ]
            print(fills[cols].head(20).to_string())
        print(f"\nPositions: {len(positions)}")
        if not positions.empty:
            cols = [
                c
                for c in [
                    "instrument_id",
                    "side",
                    "quantity",
                    "realized_pnl",
                    "realized_return",
                ]
                if c in positions.columns
            ]
            print(positions[cols].to_string())

    metrics = compute_metrics(account, fills, positions, float(starting_cash))
    metrics.update(
        {
            "symbol": symbol,
            "strategy_name": "composite",
            "preset_key": preset_key,
            "period_start": ws.test_start,
            "period_end": ws.test_end,
            "params_hash": _params_hash(symbol, preset_key, ws, preset),
            "params_json": json.dumps(
                {
                    "active_signals": list(preset["active_signals"]),
                    "buy_threshold": preset["buy_threshold"],
                    "sell_threshold": preset["sell_threshold"],
                    "stop_loss_pct": preset["stop_loss_pct"],
                    "take_profit_pct": preset["take_profit_pct"],
                }
            ),
            "created_at": datetime.now().isoformat(),
        }
    )

    engine.dispose()
    return metrics


def _save_results(rows: list[dict], parquet_path: Path) -> int:
    if not rows:
        return 0
    new_df = pd.DataFrame(rows)
    if parquet_path.exists():
        old_df = pd.read_parquet(parquet_path)
        combined = pd.concat([old_df, new_df]).drop_duplicates(
            subset=["params_hash"], keep="last"
        )
        added = len(combined) - len(old_df)
    else:
        combined = new_df
        added = len(new_df)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(parquet_path, index=False)
    return added


def _existing_hashes(parquet_path: Path) -> set[str]:
    if not parquet_path.exists():
        return set()
    df = pd.read_parquet(parquet_path, columns=["params_hash"])
    return set(df["params_hash"].astype(str))


def _cell_worker(args: tuple) -> dict:
    """ProcessPoolExecutor 워커. 예외 시 'error' 필드 포함 dict."""
    symbol, preset_key, ws_dict, starting_cash = args
    ws = WalkForwardSpec(**ws_dict)
    try:
        return run_single_cell(
            symbol, preset_key, ws, starting_cash=starting_cash, verbose=False
        )
    except Exception as exc:
        return {
            "symbol": symbol,
            "preset_key": preset_key,
            "error": f"{exc.__class__.__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strategy×Symbol fitness matrix backtest"
    )
    parser.add_argument("--symbol", help="Single symbol (overrides --pool)")
    parser.add_argument("--pool", default="default", help="default | sp500 | <SYM>")
    parser.add_argument("--preset", help="Single preset (overrides --presets)")
    parser.add_argument("--presets", default="all", help="Comma-separated, or 'all'")
    parser.add_argument(
        "--train", default="2020-01-01:2022-12-31", help="YYYY-MM-DD:YYYY-MM-DD"
    )
    parser.add_argument("--test", default="2023-01-01:2024-12-31")
    parser.add_argument("--cash", type=int, default=100_000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--inspect", action="store_true", help="Single-cell verbose mode"
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-run even if hash exists"
    )
    args = parser.parse_args()

    train_start, train_end = parse_period_arg(args.train)
    test_start, test_end = parse_period_arg(args.test)
    ws = WalkForwardSpec(train_start, train_end, test_start, test_end)
    ws_dict = {
        "train_start": train_start,
        "train_end": train_end,
        "test_start": test_start,
        "test_end": test_end,
    }

    symbols = [args.symbol.upper()] if args.symbol else get_pool(args.pool)
    if args.preset:
        preset_keys = [args.preset]
    elif args.presets == "all":
        preset_keys = list(PRESETS.keys())
    else:
        preset_keys = [p.strip() for p in args.presets.split(",")]

    cells = [(s, p, ws_dict, args.cash) for s in symbols for p in preset_keys]

    if args.inspect:
        if len(cells) != 1:
            print(
                f"--inspect requires single cell (got {len(cells)}). "
                "Use --symbol AND --preset.",
                file=sys.stderr,
            )
            return 2
        s, p, ws_d, cash = cells[0]
        print(f"INSPECT: {s} × {p}, test={test_start}~{test_end}, cash=${cash:,}\n")
        m = run_single_cell(
            s, p, WalkForwardSpec(**ws_d), starting_cash=cash, verbose=True
        )
        print("\nMetrics:")
        for k, v in m.items():
            if k.startswith("params") or k == "created_at":
                continue
            if isinstance(v, float):
                print(f"  {k:18s} {v:>12.4f}")
            else:
                print(f"  {k:18s} {v}")
        return 0

    print(
        f"Matrix: {len(symbols)} symbols × {len(preset_keys)} presets = {len(cells)} cells"
    )
    print(f"  test period: {test_start} ~ {test_end}")
    print(f"  workers: {args.workers}")

    if not args.force:
        existing = _existing_hashes(MATRIX_PARQUET)
        new_cells = []
        skipped = 0
        for c in cells:
            s, p, _, _ = c
            preset_dict = get_preset(p)
            if _params_hash(s, p, ws, preset_dict) in existing:
                skipped += 1
            else:
                new_cells.append(c)
        if skipped:
            print(
                f"  skipping {skipped} cells already in {MATRIX_PARQUET.name}; "
                "use --force to redo"
            )
        cells = new_cells

    if not cells:
        print("Nothing to run.")
        return 0

    rows: list[dict] = []
    errors: list[dict] = []
    done = 0
    total = len(cells)

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(_cell_worker, c): c for c in cells}
        for fut in as_completed(futures):
            r = fut.result()
            done += 1
            if "error" in r:
                errors.append(r)
                print(
                    f"  [{done}/{total}] ERROR {r['symbol']:6s} × {r['preset_key']:14s} : {r['error']}",
                    file=sys.stderr,
                )
            else:
                rows.append(r)
                print(
                    f"  [{done}/{total}] {r['symbol']:6s} {r['preset_key']:14s} "
                    f"fitness={r['fitness']:+.3f} sharpe={r['sharpe']:+.2f} "
                    f"MDD={r['max_drawdown']*100:>4.1f}% trades={r['total_positions']:>3d}"
                )

    added = _save_results(rows, MATRIX_PARQUET)
    print(f"\nSaved {added} new rows to {MATRIX_PARQUET}")
    print(f"Errors: {len(errors)}")

    if rows:
        df = pd.DataFrame(rows).sort_values("fitness", ascending=False).head(10)
        print("\nTop 10 by fitness:")
        cols = [
            "symbol",
            "preset_key",
            "fitness",
            "sharpe",
            "max_drawdown",
            "total_return",
            "total_positions",
        ]
        print(df[cols].to_string(index=False))

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
