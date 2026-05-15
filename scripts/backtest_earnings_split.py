"""Earnings-aware 백테스트: 진정한 non-earnings 알파를 분리 측정.

WHITELIST + Score≥N 시점의 forward returns를 두 그룹으로 분할:
  - earnings_window: 진입일 ±days 캘린더일 내 earnings 있음 (post-earnings drift / pre-earnings buildup)
  - clean: earnings 없음 (순수 모멘텀)

각 그룹의 알파/Sharpe/MDD를 비교하여:
  - 백테스트 알파의 어느 부분이 "earnings-driven" 인지 정량화
  - clean 케이스만의 진짜 시그널 효력 산출

사용 예:
    python -m scripts.backtest_earnings_split
    python -m scripts.backtest_earnings_split --score-min 4 --days 5
    python -m scripts.backtest_earnings_split --whitelist data/symbol_filter_v3_sp500.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
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
DEFAULT_FILTER = PROJECT_ROOT / "data" / "symbol_filter.json"
EARNINGS_PATH = PROJECT_ROOT / "data" / "earnings_calendar.json"


def load_earnings_lookup(path: Path) -> dict[str, list[date]]:
    """{symbol: sorted list of earnings dates (past + future)}"""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        d = json.load(f)
    out: dict[str, list[date]] = {}
    for sym, data in d.get("earnings", {}).items():
        ds: list[date] = []
        if data.get("next"):
            try:
                ds.append(date.fromisoformat(data["next"]))
            except ValueError:
                pass
        for s in data.get("past", []):
            try:
                ds.append(date.fromisoformat(s))
            except ValueError:
                pass
        if ds:
            out[sym] = sorted(set(ds))
    return out


def is_in_earnings_window(symbol: str, target_date: date, earnings_lookup: dict[str, list[date]], days: int) -> bool:
    """target_date 기준 ±days 내에 해당 종목 earnings가 있으면 True."""
    eds = earnings_lookup.get(symbol, [])
    for ed in eds:
        if abs((target_date - ed).days) <= days:
            return True
    return False


def stat_block(net: pd.Series, mdd: pd.Series, horizon: int) -> dict:
    if len(net) == 0:
        return {"n": 0}
    avg = net.mean()
    hit = (net > 0).mean()
    std = net.std()
    sharpe = (avg / std) * np.sqrt(252.0 / horizon) if std > 0 else float("nan")
    return {
        "n": int(len(net)),
        "avg_pct": avg * 100,
        "hit_pct": hit * 100,
        "sharpe": sharpe,
        "avg_mdd_pct": mdd.mean() * 100,
        "worst_pct": net.min() * 100,
        "best_pct": net.max() * 100,
    }


async def main_async(args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc)
    print("Loading bars + macro + earnings...", file=sys.stderr)
    all_bars = await load_all_bars()
    macro = await load_macro_bars()
    state = compute_regime_state(macro)
    earnings = load_earnings_lookup(EARNINGS_PATH)
    print(f"  earnings coverage: {len(earnings)} symbols", file=sys.stderr)

    s = pd.Timestamp(args.start, tz="UTC") if args.start else None
    e = pd.Timestamp(args.end, tz="UTC") if args.end else None

    print("Computing records...", file=sys.stderr)
    records = collect_records(all_bars, s, e, regime_state=state)
    records = records.dropna(subset=[f"ret_{h}d" for h in FORWARD_HORIZONS])
    if "regime_on" in records.columns:
        records = records[records["regime_on"]]

    # WHITELIST 적용
    with Path(args.whitelist).open(encoding="utf-8") as f:
        sf = json.load(f)
    wl = {r["symbol"] for r in sf.get("whitelist", [])}
    print(f"  WHITELIST: {len(wl)} symbols", file=sys.stderr)
    records = records[records["symbol"].isin(wl)]

    target = records[records["total_score"] >= args.score_min]
    if target.empty:
        print(f"No trades to analyze.", file=sys.stderr)
        return 1

    # earnings tag
    target = target.copy()
    target["entry_date"] = target.index.date
    target["earnings_window"] = target.apply(
        lambda r: is_in_earnings_window(r["symbol"], r["entry_date"], earnings, args.days), axis=1
    )
    target["earnings_coverage"] = target["symbol"].isin(earnings)

    n_total = len(target)
    n_covered = int(target["earnings_coverage"].sum())
    n_uncovered = n_total - n_covered
    n_window = int(target["earnings_window"].sum())
    n_clean = n_covered - n_window

    print()
    print(f"=== Earnings 분리 분석 (WHITELIST {len(wl)} sym, Score>={args.score_min}, regime ON, ±{args.days}d earnings window) ===")
    print(f"Total trades: {n_total:,}")
    print(f"  earnings 데이터 있음:  {n_covered:,}  ({n_covered/n_total*100:.0f}%)")
    print(f"    └ earnings ±{args.days}d 내:    {n_window:,}  ({n_window/n_total*100:.0f}%)")
    print(f"    └ clean (no earnings):    {n_clean:,}  ({n_clean/n_total*100:.0f}%)")
    print(f"  earnings 데이터 없음:  {n_uncovered:,}  ({n_uncovered/n_total*100:.0f}%)")

    h = args.horizon
    print()
    print(f"=== {h}d net forward returns 비교 (15bps cost 차감) ===")
    print()
    print(f"{'그룹':<32} {'N':>7}  {'AVG':>8} {'HIT':>7} {'SHARPE':>7}  {'AVG_MDD':>8} {'WORST':>9} {'BEST':>9}")
    print("-" * 100)

    groups = [
        ("ALL (전체 비교용)", target),
        ("clean (earnings 없음)", target[target["earnings_coverage"] & ~target["earnings_window"]]),
        ("earnings_window (±5d)", target[target["earnings_window"]]),
        ("uncovered (earnings 데이터 X)", target[~target["earnings_coverage"]]),
    ]
    for label, sub in groups:
        if len(sub) == 0:
            print(f"{label:<32} (empty)")
            continue
        st = stat_block(sub[f"ret_{h}d_net"], sub[f"mdd_{h}d"], h)
        print(
            f"{label:<32} {st['n']:>7}  "
            f"{st['avg_pct']:>+7.2f}% {st['hit_pct']:>6.1f}% {st['sharpe']:>+6.2f}  "
            f"{st['avg_mdd_pct']:>+7.2f}% {st['worst_pct']:>+8.2f}% {st['best_pct']:>+8.2f}%"
        )

    # 종목별 earnings 비중 (가장 많이 trigger된 trade가 어느 그룹에 속했는지)
    print()
    print(f"=== 종목별 earnings 비중 top 10 (window ratio 높은 순, n≥3) ===")
    grp = target.groupby("symbol").agg(
        n=("symbol", "size"),
        n_window=("earnings_window", "sum"),
        avg_net=(f"ret_{h}d_net", "mean"),
    )
    grp = grp[grp["n"] >= 3].copy()
    grp["window_pct"] = grp["n_window"] / grp["n"] * 100
    grp = grp.sort_values("window_pct", ascending=False).head(15)
    print(f"{'SYM':<6} {'N':>4} {'WINDOW':>7} {'WIN%':>6} {'AVG_NET':>8}")
    for sym, row in grp.iterrows():
        print(f"{sym:<6} {int(row['n']):>4} {int(row['n_window']):>7} {row['window_pct']:>5.0f}% {row['avg_net']*100:>+7.2f}%")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\nDone in {elapsed:.1f}s.", file=sys.stderr)
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Earnings-aware backtest split")
    p.add_argument("--start", default="2024-06-01")
    p.add_argument("--end", default=None)
    p.add_argument("--horizon", type=int, default=5, choices=[1, 5, 20])
    p.add_argument("--score-min", type=int, default=4)
    p.add_argument("--days", type=int, default=5, help="±days earnings window")
    p.add_argument("--whitelist", type=Path, default=DEFAULT_FILTER, help="symbol_filter.json (v1 default)")
    return p.parse_args()


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
