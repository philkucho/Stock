"""Universe 확장 효과 비교 — NDX-100 vs S&P 500.

build_symbol_filter를 두 universe에 각각 적용해서 WHITELIST 크기/품질 비교.
sector 분포, 평균 hit/avg, OOS forward validation 모두 포함.

전제: data/symbol_filter.json (v1, NDX-100 기반) 이 이미 생성되어 있고,
이 스크립트는 S&P 500 universe로 v3을 새로 만들고 비교 표를 출력.

사용 예:
    python -m scripts.compare_universes
    python -m scripts.compare_universes --rebuild-v3   # S&P 500 WHITELIST 재구축
    python -m scripts.compare_universes --oos-start 2025-01-01
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
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
V1_PATH = PROJECT_ROOT / "data" / "symbol_filter.json"
V3_PATH = PROJECT_ROOT / "data" / "symbol_filter_v3_sp500.json"
SECTOR_MAP_PATH = PROJECT_ROOT / "data" / "sector_map.json"


def load_whitelist(path: Path) -> set[str]:
    with path.open(encoding="utf-8") as f:
        d = json.load(f)
    return {r["symbol"] for r in d.get("whitelist", [])}


def load_sector_map() -> dict[str, str]:
    with SECTOR_MAP_PATH.open(encoding="utf-8") as f:
        d = json.load(f)
    return {sym: v.get("sector", "Unknown") for sym, v in d["mapping"].items()}


def evaluate_oos(records: pd.DataFrame, whitelist: set[str], score_min: int, horizon: int) -> dict:
    """OOS 검증: WHITELIST + score>=N + regime ON 조건 trade의 net 통계."""
    target = records[records["symbol"].isin(whitelist) & (records["total_score"] >= score_min)]
    if len(target) < 5:
        return {"n": len(target), "skip": True}
    net = target[f"ret_{horizon}d_net"]
    avg = net.mean()
    hit = (net > 0).mean()
    std = net.std()
    sharpe = (avg / std) * np.sqrt(252.0 / horizon) if std > 0 else float("nan")
    avg_mdd = target[f"mdd_{horizon}d"].mean()
    return {
        "n": len(target),
        "avg_pct": avg * 100,
        "hit_pct": hit * 100,
        "sharpe": sharpe,
        "avg_mdd_pct": avg_mdd * 100,
        "skip": False,
    }


async def main_async(args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc)

    if args.rebuild_v3:
        print("Rebuilding v3 (S&P 500 WHITELIST)...", file=sys.stderr)
        from scripts.build_symbol_filter import classify  # noqa: PLC0415

        all_bars = await load_all_bars()
        macro = await load_macro_bars()
        state = compute_regime_state(macro)
        records = collect_records(
            all_bars,
            pd.Timestamp(args.is_start, tz="UTC"),
            pd.Timestamp(args.is_end, tz="UTC"),
            regime_state=state,
        )
        records = records.dropna(subset=[f"ret_{h}d" for h in FORWARD_HORIZONS])
        records = records[records["regime_on"]]
        target = records[records["total_score"] >= 4]
        col = "ret_5d_net"
        stats = target.groupby("symbol").agg(
            n=("symbol", "size"),
            avg_ret=(col, "mean"),
            median_ret=(col, "median"),
            hit_rate=(col, lambda s: (s > 0).mean()),
            std=(col, "std"),
        )
        classified = classify(stats, 0.55, 0.005, 8)  # min_trades=8 same as v1
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config": {
                "score": 4, "score_mode": "ge", "horizon": 5,
                "hit_threshold": 0.55, "avg_threshold": 0.005, "min_trades": 8,
                "use_gross": False, "regime_gate": "filter",
                "return_basis": "net_15bps",
                "in_sample_window": f"{args.is_start} ~ {args.is_end}",
                "universe": "all DB symbols (NDX-100 + S&P 500)",
            },
            **classified,
        }
        V3_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved {V3_PATH.name}: {len(classified['whitelist'])} whitelist / "
              f"{len(classified['blacklist'])} blacklist / {len(classified['unknown'])} unknown",
              file=sys.stderr)

    # 비교 시작
    v1 = load_whitelist(V1_PATH) if V1_PATH.exists() else set()
    v3 = load_whitelist(V3_PATH) if V3_PATH.exists() else set()
    sector_map = load_sector_map() if SECTOR_MAP_PATH.exists() else {}

    print(f"\n=== WHITELIST 비교 ===")
    print(f"v1 (NDX-100, 2024-06+): {len(v1)} 종목  → {sorted(v1)}")
    print(f"v3 (S&P 500, {args.is_start}+): {len(v3)} 종목  → {sorted(v3)}")
    print(f"공통: {sorted(v1 & v3)}")
    print(f"v3에서만 (신규): {sorted(v3 - v1)}")
    print(f"v1에서만 (탈락): {sorted(v1 - v3)}")

    # Sector 분포
    print(f"\n=== v3 WHITELIST sector 분포 ===")
    if v3 and sector_map:
        sectors = Counter(sector_map.get(s, "Unknown") for s in v3)
        for s, c in sectors.most_common():
            members = sorted([sym for sym in v3 if sector_map.get(sym) == s])
            print(f"  {s:<32} {c:>3}  {', '.join(members[:5])}{'...' if len(members) > 5 else ''}")

    # OOS 검증
    if args.oos_start:
        print(f"\n=== OOS 검증 ({args.oos_start} ~ 현재, regime ON, score >= 4, post-cost 15bps) ===")
        all_bars = await load_all_bars()
        macro = await load_macro_bars()
        state = compute_regime_state(macro)
        records = collect_records(
            all_bars,
            pd.Timestamp(args.oos_start, tz="UTC"),
            None,
            regime_state=state,
        )
        records = records.dropna(subset=[f"ret_{h}d" for h in FORWARD_HORIZONS])
        records = records[records["regime_on"]]

        for label, syms in [("v1 (NDX-100)", v1), ("v3 (S&P 500)", v3), ("UNION v1∪v3", v1 | v3)]:
            r = evaluate_oos(records, syms, score_min=4, horizon=5)
            if r.get("skip"):
                print(f"  {label}: only {r['n']} trades — skip")
                continue
            print(
                f"  {label:<24} n={r['n']:>4}  avg={r['avg_pct']:>+6.2f}%  hit={r['hit_pct']:>5.1f}%  "
                f"sharpe={r['sharpe']:>+5.2f}  avg_mdd={r['avg_mdd_pct']:>+5.2f}%"
            )

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\nDone in {elapsed:.1f}s.", file=sys.stderr)
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare NDX-100 vs S&P 500 universe WHITELIST")
    p.add_argument("--rebuild-v3", action="store_true", help="S&P 500 WHITELIST 재구축")
    p.add_argument("--is-start", default="2024-06-01", help="In-sample 시작 (default 2024-06-01, v1과 동일)")
    p.add_argument("--is-end", default="2026-05-07", help="In-sample 끝")
    p.add_argument("--oos-start", default="2025-01-01", help="OOS forward validation 시작 (None=skip)")
    return p.parse_args()


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
