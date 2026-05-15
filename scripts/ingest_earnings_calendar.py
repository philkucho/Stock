"""Earnings calendar 적재 스크립트.

yfinance.Ticker.earnings_dates로 각 종목의 과거 + 향후 earnings 일자를 받아
data/earnings_calendar.json에 저장. scan_momentum.py가 ±5거래일 blackout 판정에 사용.

Webull API와 무관 (yfinance only). DB에도 쓰지 않음 (JSON 파일로 끝).

저장 구조:
{
  "generated_at": "2026-05-07T12:00:00+00:00",
  "lookback_days": 365,
  "lookahead_days": 90,
  "earnings": {
    "AAPL": {
      "next": "2026-07-30",
      "past": ["2026-04-30", "2026-01-30", ...]
    },
    "SPY": {"next": null, "past": []},
    ...
  }
}

사용 예:
    python -m scripts.ingest_earnings_calendar --symbols AAPL,MSFT
    python -m scripts.ingest_earnings_calendar --from-file scripts/nasdaq100_tickers.txt
    python -m scripts.ingest_earnings_calendar --universe ndx100
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDX100_PATH = PROJECT_ROOT / "scripts" / "nasdaq100_tickers.txt"
SP500_PATH = PROJECT_ROOT / "data" / "sp500_tickers.json"
WHITELIST_PATH = PROJECT_ROOT / "data" / "symbol_filter.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "earnings_calendar.json"

LOOKBACK_DAYS_DEFAULT = 365
LOOKAHEAD_DAYS_DEFAULT = 90
SLEEP_BETWEEN_SYMBOLS_SEC = 0.3  # yfinance rate limit 회피


def load_universe(name: str) -> list[str]:
    if name == "ndx100":
        return sorted({s.strip().upper() for s in NDX100_PATH.read_text().splitlines() if s.strip()})
    if name == "sp500":
        with SP500_PATH.open(encoding="utf-8") as f:
            payload = json.load(f)
        return sorted({s.upper() for s in payload["tickers"]})
    if name == "whitelist":
        with WHITELIST_PATH.open(encoding="utf-8") as f:
            payload = json.load(f)
        return sorted({r["symbol"] for r in payload.get("whitelist", [])})
    if name == "all":
        symbols: set[str] = set()
        for u in ("ndx100", "sp500", "whitelist"):
            symbols.update(load_universe(u))
        return sorted(symbols)
    raise ValueError(f"Unknown universe: {name!r}")


def fetch_earnings(symbol: str, lookback_days: int, lookahead_days: int) -> dict:
    """단일 종목 earnings. 실패 시 빈 결과 반환.

    yfinance의 두 source를 병행:
      - earnings_dates : 과거 + 가까운 미래 (이미 알려진 일정). 단점: '당일' 발표가 빠질 수 있음.
      - calendar       : 다음 1건의 확정 earnings 일자만. 당일 발표를 가장 잘 잡아줌.
    둘을 합쳐 next/past를 정확히 산출.
    """
    today = date.today()
    cutoff_back = today - timedelta(days=lookback_days)
    cutoff_fwd = today + timedelta(days=lookahead_days)

    dates: list[date] = []
    next_from_cal: date | None = None

    try:
        t = yf.Ticker(symbol)
        df = t.earnings_dates  # past + ~4 future earnings, DatetimeIndex
        if df is not None and not df.empty:
            for ts in df.index:
                try:
                    d = ts.tz_convert("UTC").date() if ts.tzinfo else ts.date()
                    dates.append(d)
                except Exception:
                    continue
        # calendar로 next earnings 보강 (오늘 발표 케이스 포함)
        cal = t.calendar
        if cal:
            ed = cal.get("Earnings Date")
            if isinstance(ed, list) and ed:
                next_from_cal = ed[0] if isinstance(ed[0], date) else None
            elif isinstance(ed, date):
                next_from_cal = ed
    except Exception as exc:  # noqa: BLE001
        return {"next": None, "past": [], "error": f"{type(exc).__name__}: {exc}"}

    if not dates and next_from_cal is None:
        return {"next": None, "past": []}

    # past: cutoff_back ≤ d < today  (오늘 미포함 — 오늘은 next 후보)
    past = sorted({d for d in dates if cutoff_back <= d < today})
    # future: today ≤ d ≤ cutoff_fwd  (오늘 포함)
    future = sorted({d for d in dates if today <= d <= cutoff_fwd})
    if next_from_cal is not None and today <= next_from_cal <= cutoff_fwd:
        if next_from_cal not in future:
            future.append(next_from_cal)
            future.sort()

    return {
        "next": future[0].isoformat() if future else None,
        "past": [d.isoformat() for d in past],
    }


def is_in_earnings_blackout(
    symbol: str,
    target_date: date,
    earnings_data: dict,
    days: int = 5,
) -> bool:
    """헬퍼: scan_momentum.py에서 사용. target_date가 ±days 거래일 내 earnings에 가까우면 True.

    주의: 'days' 는 영업일이 아닌 캘린더일로 단순화 (±5일은 영업일 기준 약 ±7일).
    더 정확하게 하려면 target_date 주변 거래일 인덱스 필요 — 일단 캘린더일로 작동.
    """
    sym_data = earnings_data.get(symbol, {})
    all_dates: list[str] = []
    if sym_data.get("next"):
        all_dates.append(sym_data["next"])
    all_dates.extend(sym_data.get("past", []))

    for ds in all_dates:
        try:
            ed = date.fromisoformat(ds)
        except ValueError:
            continue
        if abs((target_date - ed).days) <= days:
            return True
    return False


def main_async(symbols: list[str], lookback: int, lookahead: int, output: Path) -> int:
    started = datetime.now(timezone.utc)
    print(f"Fetching earnings for {len(symbols)} symbols (lookback={lookback}d, lookahead={lookahead}d)\n")

    earnings: dict[str, dict] = {}
    n_with_data, n_empty, n_failed = 0, 0, 0

    for i, sym in enumerate(symbols, 1):
        try:
            result = fetch_earnings(sym, lookback, lookahead)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i:>3}/{len(symbols)}] {sym}: FAILED — {type(exc).__name__}: {exc}", flush=True)
            earnings[sym] = {"next": None, "past": [], "error": str(exc)}
            n_failed += 1
            continue

        earnings[sym] = result
        if result.get("error"):
            n_failed += 1
            tag = "ERR"
        elif result["next"] or result["past"]:
            n_with_data += 1
            tag = "OK"
        else:
            n_empty += 1
            tag = "--"
        print(
            f"  [{i:>3}/{len(symbols)}] {sym:<6} [{tag}] next={result['next'] or 'n/a':<12} "
            f"past_n={len(result['past'])}",
            flush=True,
        )
        time.sleep(SLEEP_BETWEEN_SYMBOLS_SEC)

    payload = {
        "generated_at": started.isoformat(),
        "lookback_days": lookback,
        "lookahead_days": lookahead,
        "n_symbols": len(symbols),
        "earnings": earnings,
    }
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    try:
        out_label = str(output.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        out_label = str(output)
    print(
        f"\nDone in {elapsed:.1f}s. "
        f"{n_with_data} with data, {n_empty} empty, {n_failed} failed. "
        f"-> {out_label}"
    )
    return 0 if n_failed == 0 else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest yfinance earnings calendar to JSON")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbols", help="Comma-separated tickers")
    g.add_argument("--from-file", type=Path, help="Newline-separated tickers file")
    g.add_argument(
        "--universe",
        choices=["ndx100", "sp500", "whitelist", "all"],
        default="ndx100",
        help="Predefined universe (default: ndx100)",
    )
    p.add_argument("--lookback", type=int, default=LOOKBACK_DAYS_DEFAULT)
    p.add_argument("--lookahead", type=int, default=LOOKAHEAD_DAYS_DEFAULT)
    p.add_argument("--output", type=Path, default=OUTPUT_PATH)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.symbols:
        symbols = sorted({s.strip().upper() for s in args.symbols.split(",") if s.strip()})
    elif args.from_file:
        symbols = sorted({s.strip().upper() for s in args.from_file.read_text().splitlines() if s.strip()})
    else:
        symbols = load_universe(args.universe)

    if not symbols:
        print("No symbols.", file=sys.stderr)
        return 1

    return main_async(symbols, args.lookback, args.lookahead, args.output)


if __name__ == "__main__":
    sys.exit(main())
