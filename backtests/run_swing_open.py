"""Swing open-market 백테스트.

설계:
  - Entry: 09:30 시장가 (entry day open)
  - Stop: entry - 20일 ATR × 2 (일중 high/low로 hit 판정)
  - Exit: stop hit OR 5영업일 후 종가 강제
  - Source: SystemPickLog (intraday/integrated) top N

CLI:
    venv/Scripts/python.exe -m backtests.run_swing_open --start 2026-03-27 --end 2026-06-03 --top 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, stdev

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from api.db.models import SystemPickLog
from api.db.session import async_session_factory

ATR_PERIOD = 20
ATR_MULT = 2.0
HOLD_DAYS = 5
ATR_PCT_CAP = None  # set via CLI; None = no cap

logger = logging.getLogger("backtest.swing_open")
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def _fetch_daily(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    # 비교 단순화: pandas DatetimeIndex (datetime64) 유지, 비교는 pd.Timestamp로
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.DatetimeIndex(df.index).normalize()
    return df


def _atr(df: pd.DataFrame, n: int = 20) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def simulate_one(symbol: str, entry_date: date) -> dict | None:
    fetch_start = entry_date - timedelta(days=ATR_PERIOD * 2 + 14)
    fetch_end = entry_date + timedelta(days=HOLD_DAYS * 2 + 7)
    df = _fetch_daily(symbol, fetch_start.isoformat(), fetch_end.isoformat())
    if df is None or df.empty:
        return None
    entry_ts = pd.Timestamp(entry_date)
    future_idx = df.index[df.index >= entry_ts]
    if len(future_idx) == 0:
        return None
    entry_d = future_idx[0]
    if entry_d != entry_ts:
        return None  # 휴장일 pick → 다음 거래일로 자동 매핑하지 않음(보수적)
    entry_row = df.loc[entry_d]
    hist = df[df.index < entry_d]
    if len(hist) < ATR_PERIOD + 1:
        return None
    atr_v = _atr(hist, ATR_PERIOD).iloc[-1]
    if pd.isna(atr_v) or atr_v <= 0:
        return None

    entry_p = float(entry_row["open"])
    atr_pct = float(atr_v) / entry_p * 100
    if ATR_PCT_CAP is not None and atr_pct > ATR_PCT_CAP:
        return {"_filtered": True, "symbol": symbol, "atr_pct": atr_pct}
    stop_p = entry_p - ATR_MULT * float(atr_v)
    if stop_p <= 0:
        return None

    forward = df[df.index >= entry_d].head(HOLD_DAYS + 1)
    if len(forward) < 2:
        return None

    exit_p = None
    exit_d = None
    exit_reason = None
    for i, (d, row) in enumerate(forward.iterrows()):
        # entry day: open ≤ low ≤ high; intraday low가 stop을 깨면 hit
        if float(row["low"]) <= stop_p:
            exit_p = stop_p
            exit_d = d
            exit_reason = "stop_d0" if i == 0 else f"stop_d{i}"
            break

    if exit_p is None:
        target_idx = min(HOLD_DAYS, len(forward) - 1)
        exit_p = float(forward.iloc[target_idx]["close"])
        exit_d = forward.index[target_idx]
        exit_reason = "5d_close"

    pct = (exit_p / entry_p - 1) * 100
    return {
        "symbol": symbol,
        "entry_date": entry_d.date().isoformat(),
        "exit_date": exit_d.date().isoformat() if hasattr(exit_d, "date") else str(exit_d),
        "entry_p": round(entry_p, 4),
        "stop_p": round(stop_p, 4),
        "exit_p": round(exit_p, 4),
        "atr": round(float(atr_v), 4),
        "atr_pct": round(float(atr_v) / entry_p * 100, 2),
        "pct_return": round(pct, 3),
        "exit_reason": exit_reason,
    }


async def main(start: date, end: date, top_n: int, systems: list[str]) -> None:
    async with async_session_factory() as s:
        stmt = (
            select(SystemPickLog)
            .where(SystemPickLog.pick_date >= start)
            .where(SystemPickLog.pick_date <= end)
            .where(SystemPickLog.system_id.in_(systems))
            .where(SystemPickLog.rank <= top_n)
            .order_by(SystemPickLog.pick_date, SystemPickLog.rank)
        )
        picks = list((await s.execute(stmt)).scalars().all())

    print(
        f"Universe: {len(picks)} picks  systems={systems}  top<={top_n}  "
        f"window={start}~{end}"
    )

    trades: list[dict] = []
    filtered = 0
    seen: set[tuple[str, str]] = set()
    dup = 0
    for p in picks:
        key = (p.symbol, p.pick_date.isoformat())
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        r = simulate_one(p.symbol, p.pick_date)
        if r is None:
            continue
        if r.get("_filtered"):
            filtered += 1
            continue
        r["system"] = p.system_id
        r["rank"] = p.rank
        trades.append(r)
    print(
        f"Simulated trades: {len(trades)}  (dup_skipped={dup}, atr_filtered={filtered})"
    )
    if not trades:
        return

    # SPY alpha
    spy_df = _fetch_daily(
        "SPY",
        (start - timedelta(days=5)).isoformat(),
        (end + timedelta(days=HOLD_DAYS * 3)).isoformat(),
    )
    if spy_df is not None:
        for t in trades:
            try:
                ed = pd.Timestamp(t["entry_date"])
                xd = pd.Timestamp(t["exit_date"])
                spy_e = float(spy_df.loc[ed, "open"])
                spy_x = float(spy_df.loc[xd, "close"])
                t["spy_pct"] = round((spy_x / spy_e - 1) * 100, 3)
                t["alpha"] = round(t["pct_return"] - t["spy_pct"], 3)
            except (KeyError, ValueError):
                t["spy_pct"] = None
                t["alpha"] = None

    rets = [t["pct_return"] for t in trades]
    alphas = [t["alpha"] for t in trades if t.get("alpha") is not None]
    wins = sum(1 for r in rets if r > 0)
    stops = sum(1 for t in trades if t["exit_reason"].startswith("stop"))
    sample_5d = sum(1 for t in trades if t["exit_reason"] == "5d_close")

    print("\n=== AGGREGATE ===")
    print(
        f"  n={len(trades)}  win={wins}/{len(rets)} ({wins/len(rets)*100:.0f}%)"
    )
    sorted_rets = sorted(rets)
    print(
        f"  return: avg={mean(rets):+.2f}%  median={sorted_rets[len(rets)//2]:+.2f}%  "
        f"std={stdev(rets) if len(rets)>1 else 0:.2f}%  "
        f"min={min(rets):+.2f}%  max={max(rets):+.2f}%"
    )
    if alphas:
        sorted_a = sorted(alphas)
        print(
            f"  alpha:  avg={mean(alphas):+.2f}%  median={sorted_a[len(alphas)//2]:+.2f}%  "
            f"std={stdev(alphas) if len(alphas)>1 else 0:.2f}%"
        )
        # Sharpe per trade
        if stdev(alphas) > 0:
            sharpe_trade = mean(alphas) / stdev(alphas)
            # annualize assuming ~50 trades/year (intraday-derived swing top3)
            print(f"  Sharpe (per-trade): {sharpe_trade:.2f}")
    print(f"  stop hits: {stops}/{len(trades)} ({stops/len(trades)*100:.0f}%)")
    print(f"  5d holds:  {sample_5d}/{len(trades)} ({sample_5d/len(trades)*100:.0f}%)")

    # by system
    by_sys: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_sys[t["system"]].append(t)
    print("\n--- by system ---")
    for sys_, ts in by_sys.items():
        rs = [t["pct_return"] for t in ts]
        a_s = [t["alpha"] for t in ts if t.get("alpha") is not None]
        wins_s = sum(1 for r in rs if r > 0)
        a_avg = f"{mean(a_s):+.2f}%" if a_s else "N/A"
        print(
            f"  [{sys_:11}] n={len(ts):3} win={wins_s}/{len(rs)} ({wins_s/len(rs)*100:.0f}%)  "
            f"ret={mean(rs):+.2f}%  alpha={a_avg}"
        )

    # by exit reason
    by_reason: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]].append(t["pct_return"])
    print("\n--- by exit reason ---")
    for r, rs in sorted(by_reason.items()):
        print(f"  {r:12} n={len(rs):3}  avg_ret={mean(rs):+.2f}%")

    # MDD simulation (equal-weight, sequential)
    by_day: dict[date, list[float]] = defaultdict(list)
    for t in trades:
        by_day[date.fromisoformat(t["exit_date"])].append(t["pct_return"])
    sorted_days = sorted(by_day.keys())
    equity = 1.0
    equity_curve: list[tuple[date, float]] = []
    for d in sorted_days:
        daily = mean(by_day[d]) / 100.0
        equity *= 1 + daily * 0.005 / 0.02  # 0.5% risk / ~2% avg ATR stop = ~25% sizing of ret
        equity_curve.append((d, equity))
    if equity_curve:
        peak = equity_curve[0][1]
        max_dd = 0.0
        for _, e in equity_curve:
            if e > peak:
                peak = e
            dd = (e / peak - 1) * 100
            if dd < max_dd:
                max_dd = dd
        final_pct = (equity - 1) * 100
        print(
            f"\n--- equity (risk-scaled, indicative) ---  final={final_pct:+.2f}%  MDD={max_dd:.2f}%"
        )

    # Top/bottom 10 trades
    sorted_trades = sorted(trades, key=lambda t: t["pct_return"], reverse=True)
    print("\n--- top 5 ---")
    for t in sorted_trades[:5]:
        print(
            f"  {t['entry_date']} {t['symbol']:6} ret={t['pct_return']:+.2f}%  "
            f"alpha={t.get('alpha')}  reason={t['exit_reason']}"
        )
    print("--- bottom 5 ---")
    for t in sorted_trades[-5:]:
        print(
            f"  {t['entry_date']} {t['symbol']:6} ret={t['pct_return']:+.2f}%  "
            f"alpha={t.get('alpha')}  reason={t['exit_reason']}"
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, type=lambda s: date.fromisoformat(s))
    ap.add_argument("--end", required=True, type=lambda s: date.fromisoformat(s))
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--system", default="intraday,integrated")
    ap.add_argument("--atr-cap", type=float, default=None, help="ATR%% cap (e.g. 5.0)")
    args = ap.parse_args()
    if args.atr_cap is not None:
        ATR_PCT_CAP = args.atr_cap
        # rebind module-level used inside simulate_one
        import sys as _sys
        _sys.modules[__name__].ATR_PCT_CAP = args.atr_cap
    asyncio.run(main(args.start, args.end, args.top, args.system.split(",")))
