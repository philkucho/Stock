"""시그널 단일 종목 평가 CLI (검증/디버깅용).

사용:
    python -m signals.cli --symbol AAPL
    python -m signals.cli --symbol NVDA --start 2024-01-01 --end 2024-12-31
    python -m signals.cli --symbol AAPL --date 2024-12-30  # 특정 날짜 한 줄

출력: 모든 시그널의 마지막 N봉 평가표.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
import yfinance as yf

from signals import SIGNAL_REGISTRY


def fetch(symbol: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(symbol, start=start, end=end, interval="1d", progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise ValueError(f"No data for {symbol} ({start} ~ {end})")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate all signals on a symbol")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--date", help="Show only this date (YYYY-MM-DD)")
    parser.add_argument("--tail", type=int, default=10, help="Last N bars to display")
    args = parser.parse_args()

    print(f"Loaded {len(SIGNAL_REGISTRY)} signals: {sorted(SIGNAL_REGISTRY.keys())}\n")

    bars = fetch(args.symbol, args.start, args.end)
    print(f"{args.symbol}: {len(bars)} bars  ({bars.index[0].date()} ~ {bars.index[-1].date()})\n")

    # 각 시그널 vectorized 계산 → 컬럼별 시리즈 합치기
    cols: dict[str, pd.Series] = {}
    for name in sorted(SIGNAL_REGISTRY):
        spec = SIGNAL_REGISTRY[name]
        try:
            cols[name] = spec.evaluate(bars).astype(int)
        except Exception as exc:
            print(f"  [WARN] {name} failed: {exc}", file=sys.stderr)
            cols[name] = pd.Series(0, index=bars.index, dtype=int)

    table = pd.DataFrame(cols, index=bars.index)
    table.insert(0, "close", bars["close"].round(2).values)

    if args.date:
        ts = pd.Timestamp(args.date, tz="UTC")
        if ts not in table.index:
            # 가장 가까운 거래일
            ts = table.index[table.index.searchsorted(ts) - 1]
        row = table.loc[[ts]]
        print(row.T.to_string())
        score = int(row.iloc[0, 1:].sum())
        print(f"\nGross vote score: {score:+d}  (signals: {len(SIGNAL_REGISTRY)})")
    else:
        print(table.tail(args.tail).to_string())
        last = table.iloc[-1, 1:]
        score = int(last.sum())
        print(f"\nLast bar gross vote score: {score:+d}  ({last[last == 1].index.tolist()=})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
