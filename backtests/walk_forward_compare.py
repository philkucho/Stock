"""학습기간 vs 검증기간 매트릭스 비교 (Walk-forward 검증).

같은 (종목, 프리셋) 조합이 두 기간에서 모두 좋으면 신뢰. 한쪽만 좋으면 의심.

용어:
- TRAIN: 보통 더 옛 기간 (예: 2020-01-01 ~ 2022-12-31). 룩어헤드 없는 in-sample.
- TEST:  더 최근 기간 (예: 2023-01-01 ~ 2024-12-31). 진짜 out-of-sample 검증.

분류:
- ROBUST   : train fitness > 0 AND test fitness > 0 (둘 다 양호)
- OVERFIT  : train fitness < 0.2 BUT test fitness > 0.5 (검증기간 운빨 의심)
- DECAYED  : train fitness > 0.5 BUT test fitness < 0.2 (시장 변화에 적응 실패)
- WEAK     : 둘 다 fitness < 0.3 (의미 없는 조합)

사용:
    python -m backtests.walk_forward_compare \\
        --train 2020-01-01:2022-12-31 \\
        --test 2023-01-01:2024-12-31
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
MATRIX_PARQUET = REPO_ROOT / "data" / "matrix_runs.parquet"


def parse_period(s: str) -> tuple[str, str]:
    a, b = s.split(":", 1)
    return a.strip(), b.strip()


def load_period(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    sub = df[(df["period_start"] == start) & (df["period_end"] == end)].copy()
    # 같은 (symbol, preset_key) 중 fitness 최고만 (재실행 변형들 정리)
    sub = sub.sort_values("fitness", ascending=False).drop_duplicates(
        ["symbol", "preset_key"]
    )
    return sub


def classify(train_fit: float, test_fit: float) -> str:
    if train_fit >= 0.3 and test_fit >= 0.3:
        return "ROBUST"
    if train_fit < 0.2 and test_fit > 0.5:
        return "OVERFIT"
    if train_fit > 0.5 and test_fit < 0.2:
        return "DECAYED"
    return "WEAK"


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward fitness comparison")
    parser.add_argument("--train", default="2020-01-01:2022-12-31")
    parser.add_argument("--test", default="2023-01-01:2024-12-31")
    parser.add_argument("--top", type=int, default=15, help="Show top N robust")
    args = parser.parse_args()

    if not MATRIX_PARQUET.exists():
        print(f"No matrix data at {MATRIX_PARQUET}", file=sys.stderr)
        return 1

    train_start, train_end = parse_period(args.train)
    test_start, test_end = parse_period(args.test)

    df = pd.read_parquet(MATRIX_PARQUET)
    train_df = load_period(df, train_start, train_end)
    test_df = load_period(df, test_start, test_end)

    print(f"TRAIN ({train_start}..{train_end}): {len(train_df)} cells")
    print(f"TEST  ({test_start}..{test_end}): {len(test_df)} cells")

    if train_df.empty or test_df.empty:
        print("\n한쪽 기간 데이터 없음. 먼저 둘 다 매트릭스 실행:")
        print(f"  python -m backtests.run_matrix --pool default --presets all "
              f"--train 2018-01-01:2019-12-31 --test {train_start}:{train_end}")
        print(f"  python -m backtests.run_matrix --pool default --presets all "
              f"--train {train_start}:{train_end} --test {test_start}:{test_end}")
        return 1

    # 셀 단위 inner join
    merged = train_df.merge(
        test_df,
        on=["symbol", "preset_key"],
        suffixes=("_train", "_test"),
    )
    merged["delta"] = merged["fitness_test"] - merged["fitness_train"]
    merged["category"] = merged.apply(
        lambda r: classify(r["fitness_train"], r["fitness_test"]), axis=1
    )

    print(f"\nMerged cells: {len(merged)} (overlap of train × test)")

    # rank correlation (Spearman = pearson on ranks; scipy 없이도 동작)
    rho = merged["fitness_train"].rank().corr(merged["fitness_test"].rank())
    print(f"Spearman rank correlation: {rho:+.3f}  (1.0 = perfect agreement, 0 = noise)")

    # category counts
    print("\n=== Category distribution ===")
    cat_counts = merged["category"].value_counts()
    for cat in ["ROBUST", "OVERFIT", "DECAYED", "WEAK"]:
        n = int(cat_counts.get(cat, 0))
        print(f"  {cat:8s} {n:>4d}  ({n/len(merged)*100:>5.1f}%)")

    # top robust
    robust = merged[merged["category"] == "ROBUST"].sort_values(
        "fitness_test", ascending=False
    )
    print(f"\n=== TOP {args.top} ROBUST cells (둘 다 양호, 신뢰 가능) ===")
    cols = ["symbol", "preset_key", "fitness_train", "fitness_test"]
    if not robust.empty:
        out = robust.head(args.top)[cols].copy()
        out.columns = ["symbol", "preset", "train", "test"]
        out["train"] = out["train"].round(3)
        out["test"] = out["test"].round(3)
        print(out.to_string(index=False))
    else:
        print("  (none)")

    # overfit warning
    overfit = merged[merged["category"] == "OVERFIT"].sort_values(
        "delta", ascending=False
    )
    print(f"\n=== OVERFIT 의심 (검증만 좋음, 실거래 위험) ===")
    if not overfit.empty:
        out = overfit.head(10)[cols].copy()
        out.columns = ["symbol", "preset", "train", "test"]
        out["train"] = out["train"].round(3)
        out["test"] = out["test"].round(3)
        print(out.to_string(index=False))
    else:
        print("  (none)")

    # decayed
    decayed = merged[merged["category"] == "DECAYED"].sort_values(
        "delta", ascending=True
    )
    print(f"\n=== DECAYED (학습은 좋지만 검증기간 망가짐) ===")
    if not decayed.empty:
        out = decayed.head(10)[cols].copy()
        out.columns = ["symbol", "preset", "train", "test"]
        out["train"] = out["train"].round(3)
        out["test"] = out["test"].round(3)
        print(out.to_string(index=False))
    else:
        print("  (none)")

    # 종목별 best preset 일치도
    train_best = (
        train_df.sort_values("fitness", ascending=False)
        .drop_duplicates("symbol")
        .set_index("symbol")["preset_key"]
    )
    test_best = (
        test_df.sort_values("fitness", ascending=False)
        .drop_duplicates("symbol")
        .set_index("symbol")["preset_key"]
    )
    common_syms = sorted(set(train_best.index) & set(test_best.index))
    matches = sum(1 for s in common_syms if train_best[s] == test_best[s])
    print(f"\n=== Best preset agreement (per symbol) ===")
    print(
        f"  {matches}/{len(common_syms)} symbols ({matches/len(common_syms)*100:.0f}%) "
        "have same best preset in both periods"
    )

    if matches < len(common_syms):
        print("\n  Disagreements (top 10):")
        rows = []
        for s in common_syms:
            if train_best[s] != test_best[s]:
                rows.append({"symbol": s, "train_best": train_best[s], "test_best": test_best[s]})
        print(pd.DataFrame(rows).head(10).to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
