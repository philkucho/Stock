"""Stop-loss / take-profit 시뮬레이터.

backtest_scanner.py가 생산하는 (mdd, mfe, ret_*d_net) 매트릭스를 그대로 활용해서
각 종류의 exit 룰을 적용했을 때 net alpha와 sharpe가 어떻게 변하는지 측정.

근사 모델 (단순화 가정):
  - 손절: mdd_{h}d <= -stop_pct → exit 수익률 = -stop_pct (slippage 미반영, 보수적이려면 -stop_pct - 슬립)
  - 익절: mfe_{h}d >= profit_pct AND mdd_{h}d > -stop_pct → exit 수익률 = profit_pct
  - 둘 다 없으면 → ret_{h}d_net (5d close-to-close)
  - 손절과 익절 둘 다 가능하면 → 손절 우선 (timing 모름, 보수적)

한계:
  - 같은 5d 안에서 stop 먼저 hit인지 take_profit 먼저 hit인지 모름 → 손절 우선
  - trailing stop은 일별 가격 경로 필요 → 별도 시뮬 모듈에서 처리

사용 예:
    python -m scripts.simulate_stops --stop 0.03 --profit 0.05
    python -m scripts.simulate_stops --grid  # 여러 stop/profit 조합 비교
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

from scripts.backtest_scanner import (  # noqa: E402
    COST_FRAC,
    FORWARD_HORIZONS,
    collect_records,
    load_all_bars,
)
from signals.macro_regime import compute_regime_state, load_macro_bars  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILTER_PATH = PROJECT_ROOT / "data" / "symbol_filter.json"


def apply_stop_rule(
    target: pd.DataFrame,
    horizon: int,
    stop_pct: float | None,
    profit_pct: float | None,
) -> pd.Series:
    """각 trade의 net 수익률 시뮬 (손절/익절 적용 후).

    Returns: pd.Series of net returns per trade (deduct COST_FRAC at exit).
    """
    ret = target[f"ret_{horizon}d"].copy()
    mdd = target[f"mdd_{horizon}d"]
    mfe = target[f"mfe_{horizon}d"]

    out = ret.copy()

    if stop_pct is not None and stop_pct > 0:
        stop_hit = mdd <= -stop_pct
        out = out.where(~stop_hit, -stop_pct)

    if profit_pct is not None and profit_pct > 0:
        # 손절 미발생 + 익절 hit
        if stop_pct is not None and stop_pct > 0:
            profit_hit = (mfe >= profit_pct) & (mdd > -stop_pct)
        else:
            profit_hit = mfe >= profit_pct
        out = out.where(~profit_hit, profit_pct)

    # cost 차감 (round-trip)
    return out - COST_FRAC


def evaluate_rule(target: pd.DataFrame, horizon: int, stop: float | None, profit: float | None) -> dict:
    net = apply_stop_rule(target, horizon, stop, profit)
    n = len(net)
    if n == 0:
        return {"n": 0}
    avg = net.mean()
    hit = (net > 0).mean()
    std = net.std()
    sharpe = (avg / std) * np.sqrt(252.0 / horizon) if std and std > 0 else float("nan")
    worst = net.min()
    best = net.max()
    return {
        "n": int(n),
        "avg_pct": avg * 100,
        "hit_pct": hit * 100,
        "sharpe": sharpe,
        "worst_pct": worst * 100,
        "best_pct": best * 100,
    }


async def build_target(start: str | None, end: str | None, score_min: int, whitelist_only: bool) -> pd.DataFrame:
    """records 로드 → regime ON + WHITELIST + score>=N 필터 적용된 target."""
    print("Loading bars...", file=sys.stderr, flush=True)
    all_bars = await load_all_bars()
    print("Loading regime...", file=sys.stderr, flush=True)
    macro = await load_macro_bars()
    state = compute_regime_state(macro)

    s = pd.Timestamp(start, tz="UTC") if start else None
    e = pd.Timestamp(end, tz="UTC") if end else None

    print("Computing records...", file=sys.stderr, flush=True)
    records = collect_records(all_bars, s, e, regime_state=state)
    records = records.dropna(subset=[f"ret_{h}d" for h in FORWARD_HORIZONS])
    if "regime_on" in records.columns:
        records = records[records["regime_on"]]

    if whitelist_only:
        with DEFAULT_FILTER_PATH.open(encoding="utf-8") as f:
            sf = json.load(f)
        wl = {r["symbol"] for r in sf.get("whitelist", [])}
        records = records[records["symbol"].isin(wl)]

    target = records[records["total_score"] >= score_min]
    print(f"Target: {len(target):,} trades (regime ON, "
          f"{'WHITELIST, ' if whitelist_only else ''}score >= {score_min})", file=sys.stderr)
    return target


def render_grid(target: pd.DataFrame, horizon: int) -> str:
    stop_grid = [None, 0.02, 0.025, 0.03, 0.04, 0.05]
    profit_grid = [None, 0.05, 0.08, 0.10, 0.15]

    lines = [
        f"=== Stop/Profit Grid Search ({horizon}d horizon, n={len(target)}, post-cost) ===",
        "",
        f"{'STOP':>7} {'PROFIT':>7}  {'AVG':>7} {'HIT':>6} {'SHARPE':>7}  {'WORST':>7} {'BEST':>7}",
        "-" * 60,
    ]
    for stop in stop_grid:
        for profit in profit_grid:
            r = evaluate_rule(target, horizon, stop, profit)
            stop_str = f"{stop*100:.1f}%" if stop else "  off"
            profit_str = f"{profit*100:.0f}%" if profit else "  off"
            lines.append(
                f"{stop_str:>7} {profit_str:>7}  "
                f"{r['avg_pct']:>+6.2f}% {r['hit_pct']:>5.1f}% {r['sharpe']:>+6.2f}  "
                f"{r['worst_pct']:>+6.2f}% {r['best_pct']:>+6.2f}%"
            )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Simulate stop-loss / take-profit on existing records matrix")
    p.add_argument("--start", default="2024-06-01")
    p.add_argument("--end", default=None)
    p.add_argument("--horizon", type=int, default=5, choices=[1, 5, 20])
    p.add_argument("--score-min", type=int, default=4)
    p.add_argument("--no-whitelist", action="store_true", help="WHITELIST 미적용 (universe-wide)")
    p.add_argument("--stop", type=float, default=None, help="단일 stop_pct (e.g. 0.03)")
    p.add_argument("--profit", type=float, default=None, help="단일 profit_pct (e.g. 0.05)")
    p.add_argument("--grid", action="store_true", help="다양한 stop/profit 조합 격자 비교")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc)
    target = await build_target(
        args.start, args.end, args.score_min,
        whitelist_only=not args.no_whitelist,
    )
    if target.empty:
        print("No trades to simulate.", file=sys.stderr)
        return 1

    if args.grid:
        print(render_grid(target, args.horizon))
    else:
        baseline = evaluate_rule(target, args.horizon, None, None)
        with_stop = evaluate_rule(target, args.horizon, args.stop, args.profit)
        print(f"=== Single rule (stop={args.stop}, profit={args.profit}, horizon={args.horizon}d) ===")
        print(f"  baseline: avg {baseline['avg_pct']:+.2f}% hit {baseline['hit_pct']:.1f}% sharpe {baseline['sharpe']:+.2f}")
        print(f"  with rule: avg {with_stop['avg_pct']:+.2f}% hit {with_stop['hit_pct']:.1f}% sharpe {with_stop['sharpe']:+.2f}")
        delta_avg = with_stop["avg_pct"] - baseline["avg_pct"]
        delta_sharpe = with_stop["sharpe"] - baseline["sharpe"]
        print(f"  delta:    avg {delta_avg:+.2f}pp  sharpe {delta_sharpe:+.2f}")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\nDone in {elapsed:.1f}s.", file=sys.stderr)
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
