"""Episodic Pivot (EP) 백테스트 — Qullamaggie 셋업 검증 (2026-06-15 신규).

라이브 미연결. ORB 폐기(2026-06-05) 후 단타 자리를 EP로 대체할지 검증하는 용도.

설계:
  - 유니버스: universe_members(enabled, source!=blacklist)
  - 후보: 각 날짜마다 gap≥8% + 일봉RVOL≥3x + ADR(20)≥4% 통과 → gap×rvol 내림차순 top N
  - Entry: gap일 시가 (일봉 해상도 근사 — 1m은 7일만 제공, OR 돌파는 별도 검증 필요)
  - Stop : gap일 저가
  - Exit :
      forced   = N영업일 후 종가 강제 (기존 스윙과 동일 — 대조군)
      ma_trail = 50%를 +2R/5일 중 먼저 도달 시 익절 → stop을 breakeven → 잔여는 10EMA 종가
                 이탈 시 청산 (최대 20일 캡). Qullamaggie 방식 — right-tail 확보가 목적.

한계: 일봉 entry 근사. OR(5분) 돌파 정밀 검증은 최근 7일 1m 데이터로만 가능.

CLI:
    venv/Scripts/python.exe -m backtests.run_ep --start 2026-03-01 --end 2026-06-01 --top 3 --exit ma_trail
    venv/Scripts/python.exe -m backtests.run_ep --start 2026-03-01 --end 2026-06-01 --top 3 --exit forced
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, stdev

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from api.db.models import UniverseMember
from api.db.session import async_session_factory
from signals.episodic_pivot import (
    EP_ADR_PCT_MIN,
    EP_GAP_MIN,
    EP_RVOL_MIN,
    evaluate_ep,
)

ADR_LOOKBACK = 20
EMA_PERIOD = 10
FORCED_HOLD_DAYS = 5
MA_TRAIL_MAX_HOLD = 20
PARTIAL_R = 2.0          # +2R 도달 시 부분익절
PARTIAL_DAY = 5          # 또는 5일 경과 시 부분익절
RESULTS_DIR = Path(__file__).resolve().parent / "results"

logger = logging.getLogger("backtest.ep")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


def _fetch_daily(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.DatetimeIndex(df.index).normalize()
    return df


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _entry_context(df: pd.DataFrame, entry_date: date) -> tuple | None:
    """entry_d, prev_close, hist(df before entry) 반환. 휴장일/데이터 부족 시 None."""
    entry_ts = pd.Timestamp(entry_date)
    future_idx = df.index[df.index >= entry_ts]
    if len(future_idx) == 0:
        return None
    entry_d = future_idx[0]
    if entry_d != entry_ts:
        return None  # pick일이 휴장 → 보수적으로 skip
    hist = df[df.index < entry_d]
    if len(hist) < ADR_LOOKBACK + 1:
        return None
    return entry_d, float(hist["close"].iloc[-1]), hist


def screen_candidate(symbol: str, entry_date: date, df: pd.DataFrame) -> dict | None:
    """EP 게이트 평가 (df는 심볼 전체기간 일봉, 사전 로드). 미통과 시 None."""
    if df is None or df.empty:
        return None
    ctx = _entry_context(df, entry_date)
    if ctx is None:
        return None
    entry_d, prev_close, hist = ctx
    entry_row = df.loc[entry_d]
    ev = evaluate_ep(
        symbol,
        prev_close=prev_close,
        open_price=float(entry_row["open"]),
        today_volume=float(entry_row["volume"]),
        hist_daily_bars=hist,
        intraday_bars=None,  # 일봉 모드
    )
    if ev is None or not ev.gates_passed:
        return None
    return {
        "symbol": symbol,
        "entry_d": entry_d,
        "df": df,
        "gap_pct": ev.gap_pct,
        "rvol": ev.rvol,
        "adr_pct": ev.adr_pct,
        "rank_key": ev.gap_pct * ev.rvol,
    }


def simulate_exit(cand: dict, exit_mode: str) -> dict | None:
    df: pd.DataFrame = cand["df"]
    entry_d = cand["entry_d"]
    entry_row = df.loc[entry_d]
    entry_p = float(entry_row["open"])
    stop_p = float(entry_row["low"])
    r = entry_p - stop_p
    if r <= 0 or stop_p <= 0:
        return None

    forward = df[df.index >= entry_d]
    if len(forward) < 2:
        return None

    if exit_mode == "forced":
        ret_pct, exit_d, reason = _exit_forced(forward, entry_p, stop_p)
    else:
        ret_pct, exit_d, reason = _exit_ma_trail(forward, entry_p, stop_p, r)

    return {
        "symbol": cand["symbol"],
        "entry_date": entry_d.date().isoformat(),
        "exit_date": exit_d.date().isoformat() if hasattr(exit_d, "date") else str(exit_d),
        "entry_p": round(entry_p, 4),
        "stop_p": round(stop_p, 4),
        "gap_pct": round(cand["gap_pct"], 2),
        "rvol": round(cand["rvol"], 2),
        "adr_pct": round(cand["adr_pct"], 2),
        "pct_return": round(ret_pct, 3),
        "exit_reason": reason,
    }


def _exit_forced(forward: pd.DataFrame, entry_p: float, stop_p: float):
    # i==0(진입일)은 stop 체크 제외 — stop=진입일 저가라 당일은 정의상 trivially hit됨.
    # day1+ 부터 종가일 저가가 진입일 저가를 깨면 손절 (ma_trail과 동일 규칙).
    for i, (d, row) in enumerate(forward.iterrows()):
        if i == 0:
            continue
        if float(row["low"]) <= stop_p:
            return (stop_p / entry_p - 1) * 100, d, f"stop_d{i}"
    idx = min(FORCED_HOLD_DAYS, len(forward) - 1)
    exit_d = forward.index[idx]
    return (float(forward.iloc[idx]["close"]) / entry_p - 1) * 100, exit_d, "forced_close"


def _exit_ma_trail(forward: pd.DataFrame, entry_p: float, stop_p: float, r: float):
    """50% 부분익절(+2R/5일) → breakeven → 잔여 10EMA 종가 이탈 청산 (max 20일)."""
    ema = _ema(forward["close"], EMA_PERIOD)
    partial_target = entry_p + PARTIAL_R * r
    cur_stop = stop_p
    partial_taken = False
    partial_ret = 0.0  # 50% 몫의 수익률 (%)

    horizon = forward.head(MA_TRAIL_MAX_HOLD + 1)
    for i, (d, row) in enumerate(horizon.iterrows()):
        if i == 0:
            continue  # entry day
        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])

        # 1) stop 우선 (잔여분)
        if low <= cur_stop:
            rem_ret = (cur_stop / entry_p - 1) * 100
            total = (partial_ret + rem_ret) / 2 if partial_taken else rem_ret
            reason = "stop_after_partial" if partial_taken else ("stop_d%d" % i)
            return total, d, reason

        # 2) 부분익절 (아직 안 했으면): +2R 도달 또는 5일 경과
        if not partial_taken:
            if high >= partial_target:
                partial_ret = (partial_target / entry_p - 1) * 100
                partial_taken = True
                cur_stop = entry_p  # breakeven raise
            elif i >= PARTIAL_DAY:
                partial_ret = (close / entry_p - 1) * 100
                partial_taken = True
                cur_stop = entry_p

        # 3) 잔여분 10EMA 종가 이탈 청산
        ema_v = float(ema.iloc[i])
        if close < ema_v:
            rem_ret = (close / entry_p - 1) * 100
            total = (partial_ret + rem_ret) / 2 if partial_taken else rem_ret
            return total, d, "ma_trail_exit" if partial_taken else "ma_exit_nopartial"

    # max hold 도달 → 잔여 마지막 종가 청산
    last_d = horizon.index[-1]
    last_close = float(horizon.iloc[-1]["close"])
    rem_ret = (last_close / entry_p - 1) * 100
    total = (partial_ret + rem_ret) / 2 if partial_taken else rem_ret
    return total, last_d, "max_hold"


async def _load_universe() -> list[str]:
    async with async_session_factory() as s:
        stmt = (
            select(UniverseMember.symbol)
            .where(UniverseMember.enabled.is_(True))
            .where(UniverseMember.source != "blacklist")
            .distinct()
        )
        return sorted({row for row in (await s.execute(stmt)).scalars().all()})


def _trading_days(start: date, end: date) -> list[date]:
    """SPY 거래일 기준 — 백테스트 스캔 날짜."""
    spy = _fetch_daily("SPY", (start - timedelta(days=5)).isoformat(),
                       (end + timedelta(days=2)).isoformat())
    if spy is None:
        return []
    days = [d.date() for d in spy.index if start <= d.date() <= end]
    return days


async def main(start: date, end: date, top_n: int, exit_mode: str) -> None:
    symbols = await _load_universe()
    days = _trading_days(start, end)
    print(f"Universe: {len(symbols)} symbols  trading_days={len(days)}  "
          f"window={start}~{end}  exit={exit_mode}")
    print(f"Gates: gap>={EP_GAP_MIN}%  rvol>={EP_RVOL_MIN}x  adr>={EP_ADR_PCT_MIN}%  top<={top_n}")

    # 심볼당 전체기간 일봉 1회 사전 로드 (날짜마다 재다운로드 방지)
    fetch_start = (start - timedelta(days=ADR_LOOKBACK * 2 + 40)).isoformat()
    fetch_end = (end + timedelta(days=MA_TRAIL_MAX_HOLD * 2 + 10)).isoformat()
    bars_by_sym: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        df = _fetch_daily(sym, fetch_start, fetch_end)
        if df is not None and not df.empty:
            bars_by_sym[sym] = df
        if (i + 1) % 25 == 0:
            print(f"  preloaded {i+1}/{len(symbols)} symbols...")
    print(f"Preloaded daily bars for {len(bars_by_sym)}/{len(symbols)} symbols")

    trades: list[dict] = []
    per_day_counts: list[int] = []
    for d in days:
        cands = []
        for sym, df in bars_by_sym.items():
            try:
                c = screen_candidate(sym, d, df)
                if c is not None:
                    cands.append(c)
            except Exception as exc:
                logger.debug("screen %s %s: %s", sym, d, exc)
        cands.sort(key=lambda c: c["rank_key"], reverse=True)
        chosen = cands[:top_n]
        per_day_counts.append(len(chosen))
        for c in chosen:
            t = simulate_exit(c, exit_mode)
            if t is not None:
                trades.append(t)

    n_signal_days = sum(1 for c in per_day_counts if c > 0)
    print(f"Signal days: {n_signal_days}/{len(days)}  total trades: {len(trades)}")
    if not trades:
        print("No EP trades — 게이트가 너무 빡빡하거나 표본 부족.")
        return

    # SPY alpha
    spy_df = _fetch_daily("SPY", (start - timedelta(days=5)).isoformat(),
                          (end + timedelta(days=MA_TRAIL_MAX_HOLD * 3)).isoformat())
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
                t["alpha"] = None

    rets = [t["pct_return"] for t in trades]
    alphas = [t["alpha"] for t in trades if t.get("alpha") is not None]
    wins = sum(1 for r in rets if r > 0)
    sorted_rets = sorted(rets)

    print("\n=== AGGREGATE ===")
    print(f"  n={len(trades)}  win={wins}/{len(rets)} ({wins/len(rets)*100:.0f}%)")
    print(f"  return: avg={mean(rets):+.2f}%  median={sorted_rets[len(rets)//2]:+.2f}%  "
          f"std={stdev(rets) if len(rets)>1 else 0:.2f}%  "
          f"min={min(rets):+.2f}%  max={max(rets):+.2f}%")
    sharpe = None
    if alphas:
        sorted_a = sorted(alphas)
        print(f"  alpha:  avg={mean(alphas):+.2f}%  median={sorted_a[len(alphas)//2]:+.2f}%  "
              f"std={stdev(alphas) if len(alphas)>1 else 0:.2f}%")
        if len(alphas) > 1 and stdev(alphas) > 0:
            sharpe = mean(alphas) / stdev(alphas)
            print(f"  Sharpe (per-trade): {sharpe:.2f}")

    by_reason: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]].append(t["pct_return"])
    print("\n--- by exit reason ---")
    for rk, rs in sorted(by_reason.items()):
        print(f"  {rk:20} n={len(rs):3}  avg_ret={mean(rs):+.2f}%")

    sorted_trades = sorted(trades, key=lambda t: t["pct_return"], reverse=True)
    print("\n--- top 5 ---")
    for t in sorted_trades[:5]:
        print(f"  {t['entry_date']} {t['symbol']:6} ret={t['pct_return']:+.2f}%  "
              f"gap={t['gap_pct']}%  reason={t['exit_reason']}")
    print("--- bottom 5 ---")
    for t in sorted_trades[-5:]:
        print(f"  {t['entry_date']} {t['symbol']:6} ret={t['pct_return']:+.2f}%  "
              f"gap={t['gap_pct']}%  reason={t['exit_reason']}")

    # 결과 저장
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"ep_{exit_mode}_{start.isoformat()}_{end.isoformat()}.json"
    summary = {
        "params": {"start": start.isoformat(), "end": end.isoformat(), "top_n": top_n,
                   "exit_mode": exit_mode, "gap_min": EP_GAP_MIN, "rvol_min": EP_RVOL_MIN,
                   "adr_min": EP_ADR_PCT_MIN},
        "n_trades": len(trades), "win_rate": wins / len(rets),
        "avg_return_pct": mean(rets), "avg_alpha_pct": mean(alphas) if alphas else None,
        "sharpe_per_trade": sharpe, "signal_days": n_signal_days, "total_days": len(days),
        "trades": trades,
    }
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSaved → {out_path}")


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--exit", choices=["ma_trail", "forced"], default="ma_trail")
    return p.parse_args()


if __name__ == "__main__":
    a = _parse()
    asyncio.run(main(date.fromisoformat(a.start), date.fromisoformat(a.end), a.top, a.exit))
