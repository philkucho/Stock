"""v10 OOS 검증 — 2022 H1 약세장 walk-forward.

목적: v10이 60일 강세장(2026 March-May)에서 보인 Sharpe 8.81이
      약세장에서도 진성 alpha인지, 강세장 우연인지 검증.

방법:
  1. 2022-01-03 ~ 2022-06-30 walk-forward (월요일 sampling, 약 25 거래일)
  2. 각 거래일에 run_integrated_v10(target_date) 호출
  3. picks의 5d/10d outcome을 yfinance daily bars로 시뮬
  4. SPY-relative alpha + Sharpe + win rate 통계

기준:
  - Pass:    5d/10d 평균 alpha > 0 AND Sharpe > 1
  - Marginal: alpha > 0 but Sharpe < 1 (regime-gate 강화 필요)
  - Fail:    alpha < 0 (v10 retire 검토)

CLI:
  python -m scripts.v10_oos_bear_2022
  python -m scripts.v10_oos_bear_2022 --start 2022-01-03 --end 2022-06-30 --freq weekly
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()


def trading_days(start: date, end: date) -> list[date]:
    """주말 제외 (US holiday는 무시 — yfinance가 알아서 처리)."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def sample_dates(days: list[date], freq: str) -> list[date]:
    if freq == "daily":
        return days
    if freq == "weekly":
        return [d for d in days if d.weekday() == 0]  # 월요일만
    if freq == "biweekly":
        weekly = [d for d in days if d.weekday() == 0]
        return weekly[::2]
    raise ValueError(f"unknown freq: {freq}")


def simulate_pick_outcome(symbol: str, pick_date: date, horizon: int, bars_cache: dict) -> dict | None:
    """yfinance bars로 pick의 N일 outcome 시뮬.

    entry: pick_date 다음 거래일 시초가
    exit: entry_date + horizon 거래일 종가
    SPY-relative alpha = pct_return - SPY pct_return
    """
    import pandas as pd

    bars = bars_cache.get(symbol)
    spy = bars_cache.get("SPY")
    if bars is None or spy is None:
        return None
    if pick_date not in bars.index or pick_date not in spy.index:
        return None

    idx = sorted(bars.index)
    spy_idx = sorted(spy.index)
    try:
        si = idx.index(pick_date) + 1
        spy_si = spy_idx.index(pick_date) + 1
    except ValueError:
        return None
    if si >= len(idx) or si + horizon > len(idx):
        return None
    if spy_si >= len(spy_idx) or spy_si + horizon > len(spy_idx):
        return None

    entry_date = idx[si]
    spy_entry_date = spy_idx[spy_si]
    entry = float(bars.loc[entry_date, "open"])
    spy_entry = float(spy.loc[spy_entry_date, "open"])

    exit_date = idx[si + horizon - 1]
    spy_exit_date = spy_idx[spy_si + horizon - 1]
    exit_price = float(bars.loc[exit_date, "close"])
    spy_exit = float(spy.loc[spy_exit_date, "close"])

    if entry <= 0 or spy_entry <= 0:
        return None

    pct = (exit_price / entry - 1) * 100
    spy_pct = (spy_exit / spy_entry - 1) * 100
    alpha = pct - spy_pct

    # 1R = 2.5% 가정 (보수 평균)
    risk_pct = 0.025
    r_multiple = pct / (risk_pct * 100)

    return {
        "symbol": symbol, "pick_date": pick_date.isoformat(),
        "entry_date": entry_date.isoformat(), "exit_date": exit_date.isoformat(),
        "horizon": horizon, "entry": entry, "exit": exit_price,
        "pct_return": pct, "spy_pct_return": spy_pct, "alpha": alpha,
        "r_multiple": r_multiple,
    }


def fetch_bars_cache(symbols: list[str], start: date, end: date) -> dict:
    """yfinance로 모든 symbol의 daily bars 일괄 fetch."""
    import yfinance as yf
    import pandas as pd

    cache = {}
    syms_with_spy = list(set(symbols) | {"SPY"})
    for sym in syms_with_spy:
        try:
            df = yf.download(sym, start=start - timedelta(days=20), end=end + timedelta(days=20),
                             progress=False, auto_adjust=False)
            if df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.lower() for c in df.columns]
            df.index = [d.date() for d in df.index]
            cache[sym] = df
        except Exception:
            pass
    return cache


def stats_for(outcomes: list[dict], horizon: int) -> dict:
    sub = [o for o in outcomes if o["horizon"] == horizon]
    n = len(sub)
    if n == 0:
        return {"n": 0}
    rets = [o["pct_return"] for o in sub]
    alphas = [o["alpha"] for o in sub]
    spy_rets = [o["spy_pct_return"] for o in sub]
    rs = [o["r_multiple"] for o in sub]
    win = sum(1 for r in rets if r > 0) / n
    alpha_win = sum(1 for a in alphas if a > 0) / n
    avg_ret = sum(rets) / n
    avg_alpha = sum(alphas) / n
    avg_r = sum(rs) / n
    avg_spy = sum(spy_rets) / n
    std_ret = statistics.stdev(rets) if n > 1 else 0
    sharpe = (avg_ret / std_ret) * (252 / horizon) ** 0.5 if std_ret > 0 else 0
    return {
        "n": n,
        "avg_pct_return": round(avg_ret, 3),
        "avg_alpha": round(avg_alpha, 3),
        "avg_spy_return": round(avg_spy, 3),
        "avg_r_multiple": round(avg_r, 3),
        "win_rate": round(win, 3),
        "alpha_win_rate": round(alpha_win, 3),
        "std_pct_return": round(std_ret, 3),
        "sharpe_annual": round(sharpe, 2),
        "best_pct": round(max(rets), 2),
        "worst_pct": round(min(rets), 2),
    }


async def main(start: date, end: date, freq: str, top: int, output: Path | None):
    from api.db.session import async_session_factory
    from scanner.integrated.run import run_integrated_v10
    from scanner.regime import evaluate_regime

    eval_dates = sample_dates(trading_days(start, end), freq)
    print(f"OOS scan: {len(eval_dates)} eval dates ({eval_dates[0]} ~ {eval_dates[-1]})")
    print(f"Freq: {freq}, Top picks per date: {top}")
    print()

    # 1) v10 picks per date
    pick_records = []
    for i, d in enumerate(eval_dates, 1):
        r = evaluate_regime(d)
        async with async_session_factory() as s:
            t0 = time.time()
            picks = await run_integrated_v10(d, top=top, session=s)
            t = time.time() - t0
        syms = [p.symbol for p in picks]
        pick_records.append({"date": d.isoformat(), "regime_score": r.score, "regime_mode": r.mode,
                             "picks": syms, "elapsed_s": round(t, 1)})
        print(f"  [{i:>2}/{len(eval_dates)}] {d}  regime={r.score:4.1f} {r.mode:12s} picks={len(picks):2d} ({t:.0f}s) {','.join(syms) or '(none)'}")

    # 2) Bars cache (모든 picks symbols + SPY)
    all_syms = sorted({s for r in pick_records for s in r["picks"]})
    print(f"\nFetching bars for {len(all_syms)} symbols + SPY...")
    bars_cache = fetch_bars_cache(all_syms, start, end + timedelta(days=20))
    print(f"Loaded {len(bars_cache)} symbols")

    # 3) Outcomes simulation
    outcomes = []
    for rec in pick_records:
        d = date.fromisoformat(rec["date"])
        for sym in rec["picks"]:
            for h in [5, 10]:
                o = simulate_pick_outcome(sym, d, h, bars_cache)
                if o:
                    o["regime_score"] = rec["regime_score"]
                    o["regime_mode"] = rec["regime_mode"]
                    outcomes.append(o)

    # 4) Stats
    s5 = stats_for(outcomes, 5)
    s10 = stats_for(outcomes, 10)

    print()
    print("=" * 70)
    print("=== v10 OOS 2022 H1 RESULTS ===")
    print("=" * 70)
    print(f"\nTotal picks: {sum(len(r['picks']) for r in pick_records)}")
    print(f"Total outcomes: {len(outcomes)}")
    print(f"Eval dates with picks: {sum(1 for r in pick_records if r['picks'])}/{len(pick_records)}")

    for h, s in [(5, s5), (10, s10)]:
        print(f"\n--- {h}-day horizon (n={s.get('n', 0)}) ---")
        if s.get("n", 0) == 0:
            print("  (no outcomes)")
            continue
        print(f"  Avg return       : {s['avg_pct_return']:+.2f}%")
        print(f"  Avg SPY return   : {s['avg_spy_return']:+.2f}%")
        print(f"  Avg alpha        : {s['avg_alpha']:+.2f}%")
        print(f"  Avg R multiple   : {s['avg_r_multiple']:+.3f}R")
        print(f"  Win rate         : {s['win_rate']*100:.0f}%")
        print(f"  Alpha win rate   : {s['alpha_win_rate']*100:.0f}%")
        print(f"  Sharpe (annual)  : {s['sharpe_annual']:.2f}")
        print(f"  Best/Worst       : {s['best_pct']:+.2f}% / {s['worst_pct']:+.2f}%")

    # 5) Verdict
    def verdict(s):
        if s.get("n", 0) == 0: return "NO_DATA"
        if s["avg_alpha"] < 0: return "FAIL (alpha < 0 — v10 retire 검토)"
        if s["sharpe_annual"] < 1: return "MARGINAL (alpha > 0, Sharpe < 1 — regime-gate 강화 필요)"
        return "PASS (alpha > 0 AND Sharpe > 1)"

    print(f"\n--- VERDICT ---")
    print(f"  5d  : {verdict(s5)}")
    print(f"  10d : {verdict(s10)}")

    # 6) Save
    if output:
        report = {
            "params": {"start": start.isoformat(), "end": end.isoformat(), "freq": freq, "top": top},
            "pick_records": pick_records,
            "outcomes": outcomes,
            "stats_5d": s5, "stats_10d": s10,
            "verdict_5d": verdict(s5), "verdict_10d": verdict(s10),
        }
        output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nReport saved: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2022, 1, 3))
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2022, 6, 30))
    parser.add_argument("--freq", choices=["daily", "weekly", "biweekly"], default="weekly")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("logs/v10_oos_2022.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args.start, args.end, args.freq, args.top, args.output))
