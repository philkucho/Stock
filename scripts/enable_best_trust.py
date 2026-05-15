"""매트릭스 화면 ★ Enable best for all (Trust mode) 동작을 CLI/API로 재현.

- TEST 기간 매트릭스에서 종목별 best 프리셋 추출
- TRAIN 기간 fitness와 비교해 OVERFIT 셀 자동 제외
- fitness > 0인 best만 POST /api/assignments 로 enabled=true 등록
- 직전 ROBUST였다가 DECAYED로 전락한 활성 전략은 자동 비활성화
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
PARQUET = REPO_ROOT / "data" / "matrix_runs.parquet"


def classify(train_fit: float, test_fit: float) -> str:
    if train_fit >= 0.3 and test_fit >= 0.3:
        return "ROBUST"
    if train_fit < 0.2 and test_fit > 0.5:
        return "OVERFIT"
    if train_fit > 0.5 and test_fit < 0.2:
        return "DECAYED"
    return "WEAK"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", default="2025-05-01:2026-04-30")
    ap.add_argument("--train", default="2024-05-01:2025-04-30")
    ap.add_argument("--h3", default="2026-02-01:2026-04-30", help="3-month horizon")
    ap.add_argument("--h1", default="2026-04-01:2026-04-30", help="1-month horizon")
    ap.add_argument(
        "--multi-horizon",
        action="store_true",
        help="3-of-3 ≥ 0.3 충족 셀만 best 후보로 채택",
    )
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    test_start, test_end = args.test.split(":")
    train_start, train_end = args.train.split(":")
    h3_start, h3_end = args.h3.split(":")
    h1_start, h1_end = args.h1.split(":")

    df = pd.read_parquet(PARQUET)

    def _period(start: str, end: str) -> pd.DataFrame:
        return (
            df[(df.period_start == start) & (df.period_end == end)]
            .sort_values("fitness", ascending=False)
            .drop_duplicates(["symbol", "preset_key"])
        )

    test = _period(test_start, test_end)
    train = _period(train_start, train_end).set_index(["symbol", "preset_key"])["fitness"]
    h3_map = _period(h3_start, h3_end).set_index(["symbol", "preset_key"])["fitness"]
    h1_map = _period(h1_start, h1_end).set_index(["symbol", "preset_key"])["fitness"]

    # 종목별 best (OVERFIT 제외, multi-horizon이면 3-of-3 양호만)
    candidates: list[dict] = []
    for sym, group in test.groupby("symbol"):
        group = group.sort_values("fitness", ascending=False)
        best = None
        for _, row in group.iterrows():
            tr = float(train.get((row.symbol, row.preset_key), float("nan")))
            cls = classify(tr, float(row.fitness))
            if cls == "OVERFIT":
                continue
            if args.multi_horizon:
                f3 = float(h3_map.get((row.symbol, row.preset_key), float("nan")))
                f1 = float(h1_map.get((row.symbol, row.preset_key), float("nan")))
                if (
                    pd.isna(f3)
                    or pd.isna(f1)
                    or row.fitness < args.threshold
                    or f3 < args.threshold
                    or f1 < args.threshold
                ):
                    continue
                best = (row, tr, cls, f3, f1)
            else:
                best = (row, tr, cls, None, None)
            break
        if best is None:
            continue
        row, tr, cls, f3, f1 = best
        if row.fitness <= 0:
            continue
        candidates.append({
            "symbol": row.symbol,
            "preset_key": row.preset_key,
            "fitness": float(row.fitness),
            "train_fitness": tr,
            "category": cls,
            "fitness_3m": f3,
            "fitness_1m": f1,
        })

    print(f"=== Best per symbol (Trust mode, OVERFIT excluded) ===")
    print(f"Test:  {test_start} ~ {test_end}")
    print(f"Train: {train_start} ~ {train_end}")
    print(f"Candidates: {len(candidates)} symbols\n")
    cat_count: dict[str, int] = {}
    for c in candidates:
        cat_count[c["category"]] = cat_count.get(c["category"], 0) + 1
    for k, v in sorted(cat_count.items()):
        print(f"  {k}: {v}")
    print()

    if args.dry_run:
        for c in candidates:
            extra = ""
            if c["fitness_3m"] is not None:
                extra = f" 3M={c['fitness_3m']:+.2f} 1M={c['fitness_1m']:+.2f}"
            print(f"  {c['symbol']:6s} {c['preset_key']:15s} fitness={c['fitness']:+.3f} train={c['train_fitness']:+.3f}{extra} [{c['category']}]")
        return 0

    # 1) 신규 활성화
    ok = fail = 0
    skipped = 0
    print(">>> Enabling best cells via API...")
    for c in candidates:
        notes = f"auto-best ({c['category']}): test={c['fitness']:.2f} train={c['train_fitness']:.2f}"
        if c["fitness_3m"] is not None:
            notes += f" 3M={c['fitness_3m']:.2f} 1M={c['fitness_1m']:.2f}"
        body = {
            "symbol": c["symbol"],
            "preset_key": c["preset_key"],
            "enabled": True,
            "notes": notes,
        }
        try:
            r = requests.post(f"{args.api}/api/assignments", json=body, timeout=10)
            if r.status_code in (200, 201):
                ok += 1
                print(f"  ✓ {c['symbol']:6s} × {c['preset_key']:15s}  [{c['category']}]")
            else:
                fail += 1
                print(f"  ✗ {c['symbol']:6s} × {c['preset_key']:15s}  HTTP {r.status_code}: {r.text[:80]}")
        except Exception as exc:
            fail += 1
            print(f"  ✗ {c['symbol']:6s} × {c['preset_key']:15s}  {exc}")

    # 2) 새 best 후보에 들지 못한 기존 활성 페어 자동 비활성화
    print()
    print(">>> Disabling stale assignments (not in new best set)...")
    try:
        existing = requests.get(f"{args.api}/api/assignments", timeout=10).json()
    except Exception:
        existing = []
    cand_keys = {(c["symbol"], c["preset_key"]) for c in candidates}
    decayed_disabled = 0
    for a in existing:
        if not a.get("enabled"):
            continue
        key = (a["symbol"], a["preset_key"])
        if key in cand_keys:
            continue  # 새 best로 다시 켜진 것
        # 진단 정보
        tr = float(train.get(key, float("nan")))
        te_row = test[(test.symbol == a["symbol"]) & (test.preset_key == a["preset_key"])]
        te = float(te_row.iloc[0]["fitness"]) if not te_row.empty else float("nan")
        f3 = float(h3_map.get(key, float("nan")))
        f1 = float(h1_map.get(key, float("nan")))
        cls = classify(tr, te) if not pd.isna(te) else "UNKNOWN"
        reason = f"not best (test={te:.2f} 3M={f3:.2f} 1M={f1:.2f} {cls})"
        try:
            rr = requests.post(
                f"{args.api}/api/assignments/",
                json={
                    "symbol": a["symbol"],
                    "preset_key": a["preset_key"],
                    "enabled": False,
                    "notes": f"auto-disabled: {reason}",
                },
                timeout=10,
            )
            if rr.status_code in (200, 201, 204):
                decayed_disabled += 1
                print(f"  ⊘ {a['symbol']:6s} × {a['preset_key']:15s}  {reason}")
            else:
                print(f"  ✗ disable {a['symbol']} × {a['preset_key']}: HTTP {rr.status_code}")
        except Exception as exc:
            print(f"  ✗ disable {a['symbol']} × {a['preset_key']}: {exc}")

    print()
    print(f"=== Summary ===")
    print(f"Enabled:  {ok}")
    print(f"Disabled (DECAYED/WEAK): {decayed_disabled}")
    print(f"Failed:   {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
