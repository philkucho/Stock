"""Swing 모드 설계 비교 백테스트 — exit horizon × breakeven raise × position cap.

run_swing_open.py 의 시뮬 로직을 유지하면서 3개 축을 그리드 비교:
  1. Exit horizon: 5d / 7d / 10d 종가 강제 청산
  2. Breakeven raise: +1R(=2×ATR) 터치 다음날부터 stop을 entry로 raise
  3. Position cap: 무제한(기존 백테스트) vs 라이브 정책(cap 5 + sector cap 2 + 중복 보유 skip)

판정 보수성:
  - 같은 날 stop hit 과 +1R 터치가 겹치면 stop hit 우선 (pessimistic)
  - breakeven raise 는 터치 '다음 거래일'부터 적용 (당일 intraday 순서 불명)
  - equity 는 per-trade risk-scaled (0.5% risk / stop distance) 순차 복리

CLI:
    venv/Scripts/python.exe -m backtests.run_swing_compare --start 2026-03-27 --end 2026-06-03
"""
from __future__ import annotations

import argparse
import asyncio
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
ATR_PCT_CAP = 5.0  # 라이브 정책과 동일
RISK_PCT = 0.005   # SWING_RISK_PCT
POSITION_CAP = 5
SECTOR_CAP = 2

HOLDS = [5, 7, 10]

_daily_cache: dict[str, pd.DataFrame | None] = {}


def _fetch_daily(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    if symbol in _daily_cache:
        return _daily_cache[symbol]
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
    if df is None or df.empty:
        _daily_cache[symbol] = None
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.DatetimeIndex(df.index).normalize()
    _daily_cache[symbol] = df
    return df


def _atr(df: pd.DataFrame, n: int = 20) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def simulate_one(
    symbol: str,
    entry_date: date,
    fetch_start: str,
    fetch_end: str,
    hold_days: int,
    breakeven: bool,
) -> dict | None:
    df = _fetch_daily(symbol, fetch_start, fetch_end)
    if df is None or df.empty:
        return None
    entry_ts = pd.Timestamp(entry_date)
    future_idx = df.index[df.index >= entry_ts]
    if len(future_idx) == 0:
        return None
    entry_d = future_idx[0]
    if entry_d != entry_ts:
        return None  # 휴장일 pick 보수적 제외 (기존 로직 동일)
    entry_row = df.loc[entry_d]
    hist = df[df.index < entry_d]
    if len(hist) < ATR_PERIOD + 1:
        return None
    atr_v = _atr(hist, ATR_PERIOD).iloc[-1]
    if pd.isna(atr_v) or atr_v <= 0:
        return None

    entry_p = float(entry_row["open"])
    atr_pct = float(atr_v) / entry_p * 100
    if atr_pct > ATR_PCT_CAP:
        return {"_filtered": True, "symbol": symbol}
    stop_p = entry_p - ATR_MULT * float(atr_v)
    if stop_p <= 0:
        return None
    r_dist = entry_p - stop_p
    be_target = entry_p + r_dist  # +1R

    forward = df[df.index >= entry_d].head(hold_days + 1)
    if len(forward) < 2:
        return None
    truncated = len(forward) < hold_days + 1

    exit_p = exit_d = exit_reason = None
    cur_stop = stop_p
    raised = False
    raise_pending = False  # 터치 다음날부터 적용
    for i, (d, row) in enumerate(forward.iterrows()):
        if raise_pending:
            cur_stop = entry_p
            raised = True
            raise_pending = False
        # pessimistic: stop 판정 먼저
        if float(row["low"]) <= cur_stop:
            exit_p = cur_stop
            exit_d = d
            exit_reason = ("be_stop" if raised else "stop") + f"_d{i}"
            break
        if breakeven and not raised and float(row["high"]) >= be_target:
            raise_pending = True

    if exit_p is None:
        target_idx = min(hold_days, len(forward) - 1)
        exit_p = float(forward.iloc[target_idx]["close"])
        exit_d = forward.index[target_idx]
        exit_reason = f"{hold_days}d_close"

    return {
        "symbol": symbol,
        "entry_date": entry_d.date(),
        "exit_date": exit_d.date(),
        "entry_p": entry_p,
        "exit_p": exit_p,
        "atr_pct": atr_pct,
        "stop_dist_pct": r_dist / entry_p * 100,
        "pct_return": (exit_p / entry_p - 1) * 100,
        "exit_reason": exit_reason,
        "truncated": truncated,
    }


def apply_position_cap(trades: list[dict], picks_meta: dict) -> tuple[list[dict], list[dict]]:
    """라이브 run_trade 정책 재현: cap 5 + sector cap 2 + 동일 symbol 보유 중 skip.

    trades 는 (entry_date, rank) 순 정렬 입력. 보유 종료는 exit_date 종가/장중이므로
    같은 날 exit 분은 그날 09:30 신규 진입 슬롯에 반영 안 함 (보수적: exit 후 재진입은 다음날부터).
    """
    taken: list[dict] = []
    skipped: list[dict] = []
    open_pos: list[dict] = []  # {symbol, exit_date, sector}
    for t in sorted(trades, key=lambda x: (x["entry_date"], picks_meta[(x["symbol"], x["entry_date"])]["rank"])):
        ed = t["entry_date"]
        # 보유 만료 정리 — exit_date < 진입일 인 것만 해제 (당일 exit 는 슬롯 미반환)
        open_pos = [p for p in open_pos if p["exit_date"] >= ed]
        sector = picks_meta[(t["symbol"], ed)]["sector"]
        if any(p["symbol"] == t["symbol"] for p in open_pos):
            skipped.append({**t, "skip_reason": "dup_holding"})
            continue
        if len(open_pos) >= POSITION_CAP:
            skipped.append({**t, "skip_reason": "position_cap"})
            continue
        if sector and sum(1 for p in open_pos if p["sector"] == sector) >= SECTOR_CAP:
            skipped.append({**t, "skip_reason": "sector_cap"})
            continue
        open_pos.append({"symbol": t["symbol"], "exit_date": t["exit_date"], "sector": sector})
        taken.append(t)
    return taken, skipped


def summarize(trades: list[dict], spy_df: pd.DataFrame | None) -> dict:
    if not trades:
        return {"n": 0}
    for t in trades:
        if "alpha" in t:
            continue
        t["alpha"] = None
        if spy_df is not None:
            try:
                spy_e = float(spy_df.loc[pd.Timestamp(t["entry_date"]), "open"])
                spy_x = float(spy_df.loc[pd.Timestamp(t["exit_date"]), "close"])
                t["alpha"] = t["pct_return"] - (spy_x / spy_e - 1) * 100
            except (KeyError, ValueError):
                pass
    rets = [t["pct_return"] for t in trades]
    alphas = [t["alpha"] for t in trades if t["alpha"] is not None]
    wins = sum(1 for r in rets if r > 0)
    stops = sum(1 for t in trades if "stop" in t["exit_reason"])
    be_stops = sum(1 for t in trades if t["exit_reason"].startswith("be_stop"))

    # per-trade risk-scaled 순차 복리 equity
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x["exit_date"]):
        size_frac = min(RISK_PCT / (t["stop_dist_pct"] / 100), 0.30)  # 포지션 상한 30%
        equity *= 1 + (t["pct_return"] / 100) * size_frac
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity / peak - 1) * 100)

    return {
        "n": len(trades),
        "win_pct": wins / len(rets) * 100,
        "avg_ret": mean(rets),
        "avg_alpha": mean(alphas) if alphas else None,
        "sharpe": (mean(alphas) / stdev(alphas)) if len(alphas) > 1 and stdev(alphas) > 0 else None,
        "stop_pct": stops / len(trades) * 100,
        "be_stops": be_stops,
        "truncated": sum(1 for t in trades if t["truncated"]),
        "final_eq": (equity - 1) * 100,
        "mdd": max_dd,
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

    seen: set[tuple[str, date]] = set()
    uniq = []
    picks_meta: dict[tuple[str, date], dict] = {}
    for p in picks:
        key = (p.symbol, p.pick_date)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
        picks_meta[key] = {"rank": p.rank, "sector": p.sector}

    fetch_start = (start - timedelta(days=ATR_PERIOD * 2 + 14)).isoformat()
    fetch_end = (end + timedelta(days=max(HOLDS) * 2 + 7)).isoformat()
    print(f"Universe: {len(uniq)} unique picks  systems={systems}  window={start}~{end}\n")

    spy_df = _fetch_daily("SPY", fetch_start, fetch_end)

    header = (
        f"{'scenario':28} {'n':>4} {'win%':>5} {'ret':>7} {'alpha':>7} "
        f"{'Sharpe':>6} {'stop%':>5} {'beStp':>5} {'trunc':>5} {'equity':>8} {'MDD':>7}"
    )

    for cap_mode in ["uncapped", "cap5+sector2"]:
        print(f"=== {cap_mode} {'(기존 백테스트 방식)' if cap_mode == 'uncapped' else '(라이브 정책 재현)'} ===")
        print(header)
        cap_skip_summary = {}
        for hold in HOLDS:
            for be in [False, True]:
                trades = []
                filtered = 0
                for p in uniq:
                    r = simulate_one(p.symbol, p.pick_date, fetch_start, fetch_end, hold, be)
                    if r is None:
                        continue
                    if r.get("_filtered"):
                        filtered += 1
                        continue
                    trades.append(r)
                if cap_mode == "cap5+sector2":
                    trades, skipped = apply_position_cap(trades, picks_meta)
                    reasons = defaultdict(int)
                    for sk in skipped:
                        reasons[sk["skip_reason"]] += 1
                    cap_skip_summary[(hold, be)] = dict(reasons)
                stats = summarize(trades, spy_df)
                label = f"hold={hold}d be={'on ' if be else 'off'}"
                if stats["n"] == 0:
                    print(f"{label:28} {0:>4}")
                    continue
                alpha_s = f"{stats['avg_alpha']:+.2f}%" if stats["avg_alpha"] is not None else "  N/A"
                sharpe_s = f"{stats['sharpe']:.2f}" if stats["sharpe"] is not None else " N/A"
                print(
                    f"{label:28} {stats['n']:>4} {stats['win_pct']:>4.0f}% {stats['avg_ret']:>+6.2f}% "
                    f"{alpha_s:>7} {sharpe_s:>6} {stats['stop_pct']:>4.0f}% {stats['be_stops']:>5} "
                    f"{stats['truncated']:>5} {stats['final_eq']:>+7.2f}% {stats['mdd']:>6.2f}%"
                )
        if cap_skip_summary:
            print("\n  -- cap skip 사유 --")
            for (hold, be), reasons in cap_skip_summary.items():
                print(f"  hold={hold}d be={'on' if be else 'off'}: {reasons}")
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, type=lambda s: date.fromisoformat(s))
    ap.add_argument("--end", required=True, type=lambda s: date.fromisoformat(s))
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--system", default="integrated")
    args = ap.parse_args()
    asyncio.run(main(args.start, args.end, args.top, args.system.split(",")))
