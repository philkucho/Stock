"""SmaCrossPlus 파라미터 그리드 서치.

콤마 구분 파라미터 리스트를 받아 itertools.product로 조합을 생성하고,
각 조합에 대해 SmaCrossPlus 백테스트를 실행. 결과는 자동으로 backtest_runs에 저장.
모든 실행이 끝나면 DB에서 PnL 기준 top N을 조회해 콘솔에 출력.

기본 grid (3*3*3*3 = 81 조합, fast<slow 필터로 절반 정도만 실행):
    --fast 5,10,15,20
    --slow 20,30,50
    --stop-mult 1.0,1.5,2.0
    --target-mult 2.0,3.0,4.0

사용 예:
    python -m backtests.grid_search --symbol AAPL --from-db
    python -m backtests.grid_search --symbol MSFT --fast 5,10 --slow 20,30 --stop-mult 1.0,2.0 --target-mult 2.0,3.0
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from contextlib import redirect_stdout
from decimal import Decimal
from itertools import product
from typing import TypeVar

# Windows console (cp1252) 유니코드 호환
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from backtests._db_helpers import fetch_top_runs
from backtests.run_sma_cross_plus import run_backtest

T = TypeVar("T")


def parse_list(s: str, cast: type[T]) -> list[T]:
    return [cast(x.strip()) for x in s.split(",") if x.strip()]


def run_one_quiet(kwargs: dict) -> tuple[bool, str]:
    """run_backtest를 stdout 억제로 실행. (성공 여부, Summary 라인) 반환."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            run_backtest(**kwargs)
        out = buf.getvalue()
        summary = next(
            (line.strip() for line in out.splitlines() if "Summary:" in line),
            "(no positions)",
        )
        return True, summary
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    p = argparse.ArgumentParser(description="SmaCrossPlus parameter grid search")
    p.add_argument("--symbol", default="AAPL")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default="2024-12-31")

    p.add_argument("--fast", default="5,10,15,20", help="Comma-separated fast SMA periods")
    p.add_argument("--slow", default="20,30,50", help="Comma-separated slow SMA periods")
    p.add_argument(
        "--stop-mult", default="1.0,1.5,2.0", help="Comma-separated stop ATR multipliers"
    )
    p.add_argument(
        "--target-mult", default="2.0,3.0,4.0", help="Comma-separated target ATR multipliers"
    )
    p.add_argument(
        "--trailing",
        choices=["off", "on", "both"],
        default="off",
        help="Trailing stop: off, on, or both",
    )

    p.add_argument("--from-db", action="store_true")
    p.add_argument("--qty", default="10")
    p.add_argument("--cash", type=int, default=100_000)
    p.add_argument("--rsi-overbought", type=float, default=70.0)
    p.add_argument("--no-save", action="store_true", help="Skip DB save (dry run)")
    p.add_argument("--max-runs", type=int, default=120, help="Safety cap on combinations")
    p.add_argument("--top", type=int, default=10, help="Show top N at the end")
    args = p.parse_args()

    fasts = parse_list(args.fast, int)
    slows = parse_list(args.slow, int)
    stops = parse_list(args.stop_mult, float)
    targets = parse_list(args.target_mult, float)
    trailings = (
        [False]
        if args.trailing == "off"
        else [True]
        if args.trailing == "on"
        else [False, True]
    )

    combos = [
        (fast, slow, stop, target, trail)
        for fast, slow, stop, target, trail in product(fasts, slows, stops, targets, trailings)
        if fast < slow
    ]
    skipped = (
        len(fasts) * len(slows) * len(stops) * len(targets) * len(trailings) - len(combos)
    )

    print(
        f"Grid: fast={fasts} slow={slows} stop={stops} target={targets} trailing={trailings}"
    )
    print(f"  → {len(combos)} valid combos ({skipped} skipped where fast >= slow)")

    if len(combos) > args.max_runs:
        print(
            f"\n✗ Combinations ({len(combos)}) exceed --max-runs ({args.max_runs}). "
            f"Increase --max-runs or narrow the grid."
        )
        return 1

    if len(combos) == 0:
        print("\n✗ No valid combinations.")
        return 1

    print(
        f"  Symbol={args.symbol} period={args.start}~{args.end} from_db={args.from_db} "
        f"save={not args.no_save}\n"
    )

    started = time.time()
    succ = fail = 0
    for i, (fast, slow, stop, target, trail) in enumerate(combos, 1):
        kwargs = {
            "symbol": args.symbol,
            "start": args.start,
            "end": args.end,
            "fast_period": fast,
            "slow_period": slow,
            "trade_size": Decimal(args.qty),
            "starting_cash": args.cash,
            "use_rsi_filter": True,
            "rsi_period": 14,
            "rsi_overbought": args.rsi_overbought,
            "use_atr_stops": True,
            "atr_period": 14,
            "stop_atr_mult": stop,
            "target_atr_mult": target,
            "use_trailing_stop": trail,
            "from_db": args.from_db,
            "save": not args.no_save,
            "notes": (
                f"grid f={fast} s={slow} stop={stop:.1f} tgt={target:.1f} "
                f"trail={trail}"
            ),
        }
        prefix = f"[{i:>3}/{len(combos)}]"
        cfg = (
            f"f={fast:>2} s={slow:>3} stop={stop:>3.1f}x tgt={target:>3.1f}x "
            f"trail={'Y' if trail else 'N'}"
        )
        ok, info = run_one_quiet(kwargs)
        marker = "✓" if ok else "✗"
        print(f"  {prefix} {marker} {cfg}  →  {info}")
        if ok:
            succ += 1
        else:
            fail += 1

    elapsed = time.time() - started
    print(
        f"\nDone in {elapsed:.1f}s. {succ} succeeded, {fail} failed. "
        f"({elapsed / max(succ, 1):.1f}s avg per run)"
    )

    if args.no_save:
        print("\n(--no-save was set; nothing in DB to summarize)")
        return 0

    print(f"\n=== Top {args.top} by PnL ({args.symbol} {args.start}~{args.end}) ===\n")
    top = fetch_top_runs(args.symbol, args.start, args.end, limit=args.top)
    if not top:
        print("(no results in DB)")
        return 0

    header = (
        f"{'#':>3}  {'id':>4}  {'fast':>4} {'slow':>4} {'stop':>4} "
        f"{'tgt':>4} {'trail':>5}  {'PnL':>10} {'win%':>6} {'trades':>6}"
    )
    print(header)
    print("-" * len(header))
    for rank, r in enumerate(top, 1):
        params = r["params"]
        trail_s = "Y" if params.get("use_trailing_stop") else "N"
        fast = params.get("fast_period", "?")
        slow = params.get("slow_period", "?")
        stop = float(params.get("stop_atr_mult", 0))
        target = float(params.get("target_atr_mult", 0))
        print(
            f"{rank:>3}  {r['id']:>4}  {fast:>4} {slow:>4} "
            f"{stop:>4.1f} {target:>4.1f} {trail_s:>5}  "
            f"${r['pnl']:>9,.2f} {r['win_rate']*100:>5.1f}% {r['trades']:>6}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
