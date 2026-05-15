"""백테스트에서 score == TARGET 시그널을 발화한 케이스들의 상세 분석.

확인하는 것:
1. 종목별 발화 빈도 + 평균 수익률 (특정 종목 편향 여부)
2. 시점 분포 (특정 regime에 몰려있는지)
3. 수익률 분포 (mean / median / 분위수 / outlier)
4. 큰 winner/loser top 10 케이스

사용 예:
    venv/Scripts/python.exe -m scripts.analyze_top_signals
    venv/Scripts/python.exe -m scripts.analyze_top_signals --score 4
    venv/Scripts/python.exe -m scripts.analyze_top_signals --horizon 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

from scripts.backtest_scanner import (  # noqa: E402
    FORWARD_HORIZONS, collect_records, load_all_bars,
)


def by_symbol(records: pd.DataFrame, horizon: int) -> pd.DataFrame:
    col = f"ret_{horizon}d"
    g = records.groupby("symbol").agg(
        n=("symbol", "size"),
        avg_ret=(col, "mean"),
        median_ret=(col, "median"),
        hit_rate=(col, lambda s: (s > 0).mean()),
        std=(col, "std"),
    )
    g = g.sort_values("n", ascending=False)
    return g


def by_period(records: pd.DataFrame, horizon: int, freq: str) -> pd.DataFrame:
    col = f"ret_{horizon}d"
    df = records.copy()
    df["period"] = df.index.to_period(freq).astype(str)
    g = df.groupby("period").agg(
        n=("symbol", "size"),
        n_unique_symbols=("symbol", "nunique"),
        avg_ret=(col, "mean"),
        hit_rate=(col, lambda s: (s > 0).mean()),
    )
    return g


def distribution_stats(records: pd.DataFrame) -> dict:
    out = {}
    for h in FORWARD_HORIZONS:
        col = f"ret_{h}d"
        s = records[col].dropna()
        out[h] = {
            "n": len(s),
            "mean": s.mean(),
            "median": s.median(),
            "std": s.std(),
            "p10": s.quantile(0.10),
            "p25": s.quantile(0.25),
            "p75": s.quantile(0.75),
            "p90": s.quantile(0.90),
            "min": s.min(),
            "max": s.max(),
            "hit": (s > 0).mean(),
        }
    return out


def render_symbol_table(g: pd.DataFrame, horizon: int, top_n: int) -> str:
    lines = [
        f"=== Symbol-level breakdown (sorted by frequency, ret_{horizon}d) ===",
        f"{'SYM':<6} {'N':>4} {'AVG':>8} {'MEDIAN':>8} {'HIT':>6} {'STD':>8}",
        "-" * 48,
    ]
    for sym, r in g.head(top_n).iterrows():
        lines.append(
            f"{sym:<6} {int(r['n']):>4} "
            f"{r['avg_ret']*100:>+7.2f}% {r['median_ret']*100:>+7.2f}% "
            f"{r['hit_rate']*100:>5.1f}% {r['std']*100:>7.2f}%"
        )
    if len(g) > top_n:
        lines.append(f"... and {len(g) - top_n} more symbols")
    return "\n".join(lines)


def render_period_table(p: pd.DataFrame, horizon: int) -> str:
    lines = [
        f"=== Period distribution (ret_{horizon}d) ===",
        f"{'PERIOD':<8} {'N':>4} {'UNIQ':>5} {'AVG':>8} {'HIT':>6}",
        "-" * 38,
    ]
    for period, r in p.iterrows():
        lines.append(
            f"{period:<8} {int(r['n']):>4} {int(r['n_unique_symbols']):>5} "
            f"{r['avg_ret']*100:>+7.2f}% {r['hit_rate']*100:>5.1f}%"
        )
    return "\n".join(lines)


def render_distribution(stats: dict) -> str:
    lines = ["=== Forward return distribution ===",
             f"{'HORIZON':>8} {'MEAN':>8} {'MEDIAN':>8} {'P10':>8} {'P25':>8} {'P75':>8} {'P90':>8} {'MIN':>8} {'MAX':>8}",
             "-" * 80]
    for h, s in stats.items():
        lines.append(
            f"{f'{h}d':>8} "
            f"{s['mean']*100:>+7.2f}% {s['median']*100:>+7.2f}% "
            f"{s['p10']*100:>+7.2f}% {s['p25']*100:>+7.2f}% "
            f"{s['p75']*100:>+7.2f}% {s['p90']*100:>+7.2f}% "
            f"{s['min']*100:>+7.2f}% {s['max']*100:>+7.2f}%"
        )
    return "\n".join(lines)


def render_top_cases(records: pd.DataFrame, horizon: int, n: int = 10) -> str:
    col = f"ret_{horizon}d"
    sorted_recs = records.sort_values(col, ascending=False)

    def fmt(rows: pd.DataFrame, label: str) -> list[str]:
        out = [f"--- {label} ---", f"{'DATE':<12} {'SYM':<6} {'RET':>8}"]
        for ts, r in rows.iterrows():
            out.append(f"{ts.date()!s:<12} {r['symbol']:<6} {r[col]*100:>+7.2f}%")
        return out

    lines = [f"=== Top winners/losers by ret_{horizon}d ==="]
    lines.extend(fmt(sorted_recs.head(n), f"TOP {n} WINNERS"))
    lines.append("")
    lines.extend(fmt(sorted_recs.tail(n).iloc[::-1], f"TOP {n} LOSERS"))
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--score", type=int, default=5, help="분석할 score (default 5)")
    p.add_argument("--horizon", type=int, default=5, choices=FORWARD_HORIZONS,
                   help="주 분석 horizon (default 5d)")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--start", help="시작 날짜 YYYY-MM-DD")
    p.add_argument("--end", help="종료 날짜 YYYY-MM-DD")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc)
    print("Loading bars...", file=sys.stderr, flush=True)
    all_bars = await load_all_bars()

    start = pd.Timestamp(args.start, tz="UTC") if args.start else None
    end = pd.Timestamp(args.end, tz="UTC") if args.end else None

    print("Computing signals + forward returns...", file=sys.stderr, flush=True)
    records = collect_records(all_bars, start, end)
    records = records.dropna(subset=[f"ret_{h}d" for h in FORWARD_HORIZONS])

    target = records[records["total_score"] == args.score]
    if target.empty:
        print(f"No records with score == {args.score}", file=sys.stderr)
        return 1

    print(f"\nFound {len(target)} cases of score == {args.score}")
    print(f"Across {target['symbol'].nunique()} unique symbols")
    print(f"Date range: {target.index.min().date()} ~ {target.index.max().date()}")
    print()

    # 1. distribution
    print(render_distribution(distribution_stats(target)))
    print()

    # 2. by symbol
    print(render_symbol_table(by_symbol(target, args.horizon), args.horizon, args.top))
    print()

    # 3. by period
    period_freq = "M" if (target.index.max() - target.index.min()).days > 90 else "W"
    print(render_period_table(by_period(target, args.horizon, period_freq), args.horizon))
    print()

    # 4. top cases
    print(render_top_cases(target, args.horizon, n=10))

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\nDone in {elapsed:.1f}s.", file=sys.stderr)
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
