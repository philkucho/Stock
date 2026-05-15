"""월 1회 매트릭스 갱신 — Rolling Window.

오늘 날짜 기준:
- Train: 24개월 전 ~ 12개월 전 (12개월 길이)
- Test:  12개월 전 ~ 오늘   (12개월 길이)

매월 1일 (또는 임의 시점) 실행 시 윈도우가 자동으로 슬라이딩.

사용:
    python -m scripts.monthly_refresh
    python -m scripts.monthly_refresh --pool default --presets all
    python -m scripts.monthly_refresh --refresh-cache  # yfinance 새로 받기
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def months_ago(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        y -= 1
        m += 12
    # 월말 보정 (간단히 1일로 정규화)
    return date(y, m, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="Monthly matrix refresh (rolling window)")
    ap.add_argument("--pool", default="default")
    ap.add_argument("--presets", default="all")
    ap.add_argument("--train-months", type=int, default=12, help="Train window length")
    ap.add_argument("--test-months", type=int, default=12, help="Test window length")
    ap.add_argument(
        "--refresh-cache",
        action="store_true",
        help="yfinance로 캐시 강제 갱신 (느림, 월 1회 권장)",
    )
    ap.add_argument(
        "--anchor",
        default=None,
        help="기준일 (기본: 오늘). YYYY-MM-DD",
    )
    args = ap.parse_args()

    today = date.fromisoformat(args.anchor) if args.anchor else date.today()
    test_end = today.replace(day=1) - timedelta(days=1)  # 직전 월말
    test_start = months_ago(test_end, args.test_months - 1).replace(day=1)
    train_end = test_start - timedelta(days=1)
    train_start = months_ago(train_end, args.train_months - 1).replace(day=1)

    print(f"=== Monthly refresh ({today}) ===")
    print(f"Train: {train_start} ~ {train_end}")
    print(f"Test : {test_start} ~ {test_end}")
    print()

    # 1) 캐시 갱신 (옵션)
    if args.refresh_cache:
        print(">>> Refreshing yfinance cache...")
        from backtests.data_cache import get_pool, refresh_cache

        results = refresh_cache(get_pool(args.pool))
        ok = sum(1 for v in results.values() if v == "ok")
        print(f"   Cache refreshed: {ok}/{len(results)} ok")
        print()

    # 2) 매트릭스 실행 — run_matrix는 --test 기간만 백테스트.
    #    Walk-forward를 위해선 train 기간도 따로 매트릭스로 채워야 함.
    #    그래서 두 번 호출:
    #      (a) test = train 기간   (그 이전 기간을 train으로 더미 지정)
    #      (b) test = test 기간    (a)의 train을 train으로 지정)
    pre_train_end = train_start - timedelta(days=1)
    pre_train_start = months_ago(pre_train_end, args.train_months - 1).replace(day=1)

    train_arg = f"{train_start.isoformat()}:{train_end.isoformat()}"
    test_arg = f"{test_start.isoformat()}:{test_end.isoformat()}"

    runs = [
        (
            "Train period",
            f"{pre_train_start.isoformat()}:{pre_train_end.isoformat()}",
            train_arg,
        ),
        ("Test period", train_arg, test_arg),
    ]
    for label, t_arg, te_arg in runs:
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "backtests.run_matrix",
            "--pool",
            args.pool,
            "--presets",
            args.presets,
            "--train",
            t_arg,
            "--test",
            te_arg,
        ]
        print(f">>> Running matrix ({label}):")
        print("   " + " ".join(cmd))
        print()
        rc = subprocess.call(cmd, cwd=REPO_ROOT)
        if rc != 0:
            print(f"!!! run_matrix failed for {label} (rc={rc})", file=sys.stderr)
            return rc

    # 3) Walk-forward 비교 출력
    print()
    print(">>> Walk-forward comparison:")
    cmd2 = [
        sys.executable,
        "-m",
        "backtests.walk_forward_compare",
        "--train",
        train_arg,
        "--test",
        test_arg,
        "--top",
        "15",
    ]
    rc2 = subprocess.call(cmd2, cwd=REPO_ROOT)
    return rc2


if __name__ == "__main__":
    sys.exit(main())
