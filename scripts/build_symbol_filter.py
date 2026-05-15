"""종목별 시그널 효력 자동 필터.

각 종목의 historical score == TARGET 시점의 forward return을 분석해서:
- 통과: hit_rate ≥ HIT_TH AND avg_ret ≥ AVG_TH AND n ≥ MIN_TRADES → WHITELIST
- 미달: 위 조건 미충족 + n ≥ MIN_TRADES → BLACKLIST (시그널이 작동 안 하는 종목)
- 보류: n < MIN_TRADES → UNKNOWN (표본 부족)

결과는 콘솔 출력 + data/symbol_filter.json 저장.
scan_momentum.py에서 이 파일을 읽어 whitelist만 거래 가능.

사용 예:
    venv/Scripts/python.exe -m scripts.build_symbol_filter
    venv/Scripts/python.exe -m scripts.build_symbol_filter --score 4 --hit 0.55
    venv/Scripts/python.exe -m scripts.build_symbol_filter --min-trades 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

from scripts.backtest_scanner import collect_records, load_all_bars  # noqa: E402
from signals.macro_regime import compute_regime_state, load_macro_bars  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "symbol_filter.json"


def classify(stats: pd.DataFrame, hit_th: float, avg_th: float, min_trades: int) -> dict:
    whitelist: list[dict] = []
    blacklist: list[dict] = []
    unknown: list[dict] = []

    for sym, row in stats.iterrows():
        rec = {
            "symbol": sym,
            "n": int(row["n"]),
            "avg_ret": float(row["avg_ret"]),
            "hit_rate": float(row["hit_rate"]),
            "median_ret": float(row["median_ret"]),
            "std": float(row["std"]) if pd.notna(row["std"]) else None,
        }
        if row["n"] < min_trades:
            unknown.append(rec)
        elif row["hit_rate"] >= hit_th and row["avg_ret"] >= avg_th:
            whitelist.append(rec)
        else:
            blacklist.append(rec)

    whitelist.sort(key=lambda r: (r["hit_rate"], r["avg_ret"]), reverse=True)
    blacklist.sort(key=lambda r: (r["hit_rate"], r["avg_ret"]))
    unknown.sort(key=lambda r: r["symbol"])
    return {"whitelist": whitelist, "blacklist": blacklist, "unknown": unknown}


def render_group(label: str, rows: list[dict], horizon: int) -> str:
    if not rows:
        return f"=== {label} (empty) ==="
    lines = [
        f"=== {label} ({len(rows)} symbols) ===",
        f"{'SYM':<6} {'N':>4} {f'AVG_{horizon}D':>9} {f'HIT_{horizon}D':>8} {f'MEDIAN':>8}",
        "-" * 42,
    ]
    for r in rows:
        lines.append(
            f"{r['symbol']:<6} {r['n']:>4} "
            f"{r['avg_ret']*100:>+8.2f}% {r['hit_rate']*100:>7.1f}% "
            f"{r['median_ret']*100:>+7.2f}%"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build whitelist/blacklist from per-symbol signal efficacy")
    # 디폴트는 P0 정직성 기준: score 4 (≥)로 빈도 확보, net 수익률 기반, n≥8로 표본 신뢰성
    p.add_argument("--score", type=int, default=4, help="분석 대상 최소 score (default 4)")
    p.add_argument("--score-mode", choices=["eq", "ge"], default="ge", help="eq: score 정확히 일치 / ge: score 이상 (default ge)")
    p.add_argument("--horizon", type=int, default=5, choices=[1, 5, 20], help="forward horizon (default 5)")
    p.add_argument("--hit", type=float, default=0.55, help="hit_rate 임계 (default 0.55)")
    p.add_argument("--avg", type=float, default=0.005, help="avg_ret 임계 (default 0.005 = 0.5%)")
    p.add_argument("--min-trades", type=int, default=8, help="통계 신뢰 최소 거래 수 (default 8)")
    p.add_argument("--use-gross", action="store_true", help="gross return 기준 (default: 거래비용 차감 net)")
    p.add_argument("--regime-gate", choices=["off", "filter"], default="filter", help="filter: regime ON 일자만 사용 (default) / off")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="출력 JSON 경로")
    p.add_argument("--start", help="분석 시작 날짜 YYYY-MM-DD")
    p.add_argument("--end", help="분석 종료 날짜 YYYY-MM-DD")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc)
    print("Loading bars...", file=sys.stderr, flush=True)
    all_bars = await load_all_bars()

    regime_state = None
    if args.regime_gate == "filter":
        print("Loading macro regime (SPY/^VIX)...", file=sys.stderr, flush=True)
        macro = await load_macro_bars()
        regime_state = compute_regime_state(macro, fallback_when_missing=True)

    start = pd.Timestamp(args.start, tz="UTC") if args.start else None
    end = pd.Timestamp(args.end, tz="UTC") if args.end else None

    print("Computing signals + forward returns...", file=sys.stderr, flush=True)
    records = collect_records(all_bars, start, end, regime_state=regime_state)
    records = records.dropna(subset=[f"ret_{args.horizon}d"])

    if args.regime_gate == "filter" and "regime_on" in records.columns:
        before = len(records)
        records = records[records["regime_on"]]
        print(f"  regime gate: {before:,} → {len(records):,} (kept ON-only)", file=sys.stderr)

    if args.score_mode == "eq":
        target = records[records["total_score"] == args.score]
    else:
        target = records[records["total_score"] >= args.score]
    if target.empty:
        print(f"No records with score {args.score_mode} {args.score}", file=sys.stderr)
        return 1

    col_suffix = "" if args.use_gross else "_net"
    col = f"ret_{args.horizon}d{col_suffix}"
    if col not in target.columns:
        # 후방호환: net 미존재시 gross로 폴백
        col = f"ret_{args.horizon}d"
        col_suffix = " (gross fallback)"
    stats = target.groupby("symbol").agg(
        n=("symbol", "size"),
        avg_ret=(col, "mean"),
        median_ret=(col, "median"),
        hit_rate=(col, lambda s: (s > 0).mean()),
        std=(col, "std"),
    )

    classified = classify(stats, args.hit, args.avg, args.min_trades)

    return_basis = "GROSS" if args.use_gross else "NET (15bps)"
    print(f"Score {args.score_mode} {args.score} 효력 분석  ({return_basis} {args.horizon}d, "
          f"기준: hit≥{args.hit*100:.0f}% AND avg≥{args.avg*100:.2f}%, "
          f"min_trades={args.min_trades}, regime={args.regime_gate})")
    print(f"Total signals: {len(target)}  across {target['symbol'].nunique()} symbols")
    print()
    print(render_group("WHITELIST", classified["whitelist"], args.horizon))
    print()
    print(render_group("BLACKLIST", classified["blacklist"], args.horizon))
    print()
    print(render_group("UNKNOWN (표본 부족)", classified["unknown"], args.horizon))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "score": args.score,
            "score_mode": args.score_mode,
            "horizon": args.horizon,
            "hit_threshold": args.hit,
            "avg_threshold": args.avg,
            "min_trades": args.min_trades,
            "use_gross": args.use_gross,
            "regime_gate": args.regime_gate,
            "return_basis": "gross" if args.use_gross else "net_15bps",
        },
        **classified,
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\nWrote {args.out.relative_to(PROJECT_ROOT)} ({len(classified['whitelist'])} whitelist, "
          f"{len(classified['blacklist'])} blacklist, {len(classified['unknown'])} unknown). "
          f"Done in {elapsed:.1f}s.", file=sys.stderr)
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
