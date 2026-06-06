"""중장기(3~12개월) 보유 종목 선정 백테스트.

설계 (2026-06-05 도입, [[swing-mode-v1]] 후속):
  Source     : S&P 500 universe (data/sp500_full.json)
  Frequency  : Monthly rebalance (월 첫 거래일)
  Gates      : Stage 2 trend template (8조건 AND) + above MA200 + near 52w high (10%) + $20M ADV
  Score(90)  : RS IBD percentile (40) + 12mo momentum (25) + 200SMA 상대거리 (15) + Stage2 c6/c7/c8 margin (10)
               * Sector 페널티 (10점)은 v2에서 추가 — sector 데이터 외부 fetch 차단으로 생략
  Rebalance  : 60/40 turnover cap (직전 top 6 보존, 신규 4 슬롯 교체)
  Position   : top 10 균등 가중 (10% each)
  Hold       : 다음 monthly rebalance 까지 보유
  Regime gate: SPY 200MA 아래 시 신규 매수 0, 기존 holdings 유지

검증선 ([[2-tier-partial-exit]] 패턴):
  - 연간 Sharpe ≥ 0.8
  - 연간 alpha (vs SPY) ≥ 5%
  - MDD ≤ -25%
  - Monthly turnover ≤ 50%

CLI:
    venv/Scripts/python.exe -m backtests.run_longterm \
        --start 2018-01-01 --end 2024-12-31 --top 10
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, stdev

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from backtests.data_cache import get_bars
from signals.relative_strength import rs_ibd_raw
from signals.stage2_trend_template import trend_template_diagnostic, trend_template_pass

REPO_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger("backtest.longterm")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ─── 설계 상수 ───
ADV_MIN_DOLLARS = 20_000_000.0  # 30일 평균 거래대금 $20M
NEAR_52W_HIGH_PCT = 0.10        # 52주 고가의 -10% 이내 (원안 -3%에서 완화)
RS_PCT_WEIGHT = 40.0
MOM_12MO_WEIGHT = 25.0
SMA200_DIST_WEIGHT = 15.0
STAGE2_MARGIN_WEIGHT = 10.0
TURNOVER_CAP_KEEP = 6           # 직전 top 10 중 최대 6 보존 (60% keep)
SECTOR_PENALTY = 0.0            # v1: 생략 (10점), v2에서 추가
SMA200_FAR_PCT = 0.50           # +50% 위면 감점 시작
ATR_MULT_RANGE = (0.01, 0.12)   # placeholder, longterm은 ATR 게이트 미사용
REGIME_SPY_SMA = 200            # SPY 200SMA 아래 시 신규 차단


def _trading_first_day_of_month(year: int, month: int, spy_index: pd.DatetimeIndex) -> pd.Timestamp | None:
    """해당 월의 첫 거래일 (SPY 인덱스 기준)."""
    target = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    next_month = target + pd.offsets.MonthBegin(1)
    in_month = spy_index[(spy_index >= target) & (spy_index < next_month)]
    return in_month[0] if len(in_month) > 0 else None


def _generate_rebalance_dates(start: date, end: date, spy_index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """매월 첫 거래일 리스트."""
    out: list[pd.Timestamp] = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        ts = _trading_first_day_of_month(cur.year, cur.month, spy_index)
        if ts is not None and ts.date() >= start:
            out.append(ts)
        # 다음 달
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def _evaluate_symbol(
    symbol: str,
    bars: pd.DataFrame,
    eval_ts: pd.Timestamp,
) -> dict | None:
    """단일 종목 평가 — 게이트 통과 시 score components, 아니면 None.

    eval_ts 시점까지의 일봉만 사용 (look-ahead 방지).
    Returns: {rs_raw, mom_12mo, sma200_dist, c_margin, gates_pass, last_close, adv_30d}
    """
    hist = bars[bars.index < eval_ts]  # eval_ts는 entry 당일 — 평가는 어제까지 close
    if len(hist) < 252:
        return None

    last = hist.iloc[-1]
    last_close = float(last["close"])

    # ── 게이트 ──
    # Stage 2 trend template
    stage2 = bool(trend_template_pass(hist).iloc[-1])
    if not stage2:
        return None

    # above MA200
    sma200 = float(hist["close"].rolling(200).mean().iloc[-1])
    if last_close <= sma200:
        return None

    # near 52w high (-10% 이내 = high * 0.90 이상)
    high_52w = float(hist["high"].rolling(252).max().iloc[-1])
    if last_close < high_52w * (1.0 - NEAR_52W_HIGH_PCT):
        return None

    # ADV (30일 평균 $거래대금) ≥ $20M
    last_30 = hist.tail(30)
    adv_30d = float((last_30["close"] * last_30["volume"]).mean())
    if adv_30d < ADV_MIN_DOLLARS:
        return None

    # ── 점수 component ──
    rs_raw = float(rs_ibd_raw(hist).iloc[-1])
    close_252 = float(hist["close"].iloc[-252])
    mom_12mo = (last_close / close_252) - 1.0 if close_252 > 0 else 0.0
    sma200_dist = (last_close / sma200) - 1.0

    # Stage 2 진단: c6/c7/c8 마진 (200SMA 상승률, 52w 저점 +30%, 52w 고가 -25%)
    diag = trend_template_diagnostic(hist)
    sma200_prev = float(hist["close"].rolling(200).mean().iloc[-22]) if len(hist) >= 222 else sma200
    c6_margin = (sma200 / sma200_prev) - 1.0 if sma200_prev > 0 else 0.0
    low_52w = float(hist["low"].rolling(252).min().iloc[-1])
    c7_margin = (last_close / (low_52w * 1.30)) - 1.0 if low_52w > 0 else 0.0
    c8_margin = (high_52w * 0.75) / last_close if last_close > 0 else 0.0  # 1.0이면 정확히 -25%
    c_margin_score = (
        max(0.0, min(1.0, c6_margin / 0.05)) +   # 5%/m sma200 uptrend = 1.0
        max(0.0, min(1.0, c7_margin / 0.50)) +   # 52w low +30% 기준 +50% 추가 마진 = 1.0
        max(0.0, min(1.0, c8_margin - 1.0))      # high의 75%↑ = 양수
    ) / 3.0

    return {
        "symbol": symbol,
        "last_close": last_close,
        "rs_raw": rs_raw,
        "mom_12mo": mom_12mo,
        "sma200_dist": sma200_dist,
        "c_margin": c_margin_score,
        "adv_30d": adv_30d,
        "high_52w": high_52w,
        "stage2_diag": diag,
    }


def _score_candidates(candidates: list[dict]) -> list[dict]:
    """Universe 통과 종목 → composite score 추가.

    RS는 universe 내 percentile (1~99). 다른 component는 saturating 함수.
    """
    if not candidates:
        return []

    raws = [c["rs_raw"] for c in candidates]
    sorted_raws = sorted(raws)
    n = len(sorted_raws)

    for c in candidates:
        # RS percentile (1~99)
        below = sum(1 for r in sorted_raws if r < c["rs_raw"])
        rs_pct = max(1, min(99, round((below / n) * 99) + 1))
        c["rs_pct"] = rs_pct

        # 12mo momentum: saturate at +100% = 1.0
        mom_norm = max(0.0, min(1.0, c["mom_12mo"] / 1.0))

        # 200SMA 상대거리: 0~+50%까지 +, 50~100% 감점 (extended)
        d = c["sma200_dist"]
        if d <= SMA200_FAR_PCT:
            sma_norm = max(0.0, min(1.0, d / SMA200_FAR_PCT))
        else:
            # +50% 넘어가면 점수 깎임
            over = (d - SMA200_FAR_PCT) / SMA200_FAR_PCT
            sma_norm = max(0.0, 1.0 - over)

        composite = (
            (rs_pct / 99.0) * RS_PCT_WEIGHT
            + mom_norm * MOM_12MO_WEIGHT
            + sma_norm * SMA200_DIST_WEIGHT
            + c["c_margin"] * STAGE2_MARGIN_WEIGHT
        )
        c["composite"] = composite

    return sorted(candidates, key=lambda x: x["composite"], reverse=True)


def _apply_turnover_cap(
    new_top: list[dict],
    prev_holdings: list[str],
    top_n: int,
    keep_max: int,
) -> list[dict]:
    """60/40 룰: 직전 holdings 중 new_top[:top_n*2] 안에 들면 우선 보존 (최대 keep_max개)."""
    if not prev_holdings:
        return new_top[:top_n]

    extended_pool_syms = {c["symbol"] for c in new_top[:top_n * 2]}
    kept = [c for c in new_top if c["symbol"] in prev_holdings and c["symbol"] in extended_pool_syms]
    kept = kept[:keep_max]

    kept_syms = {c["symbol"] for c in kept}
    fresh = [c for c in new_top if c["symbol"] not in kept_syms]

    final = (kept + fresh)[:top_n]
    return final


def _evaluate_regime(spy_bars: pd.DataFrame, eval_ts: pd.Timestamp) -> bool:
    """SPY 200SMA 아래 시 defensive (True 반환 = 신규 차단)."""
    hist = spy_bars[spy_bars.index < eval_ts]
    if len(hist) < REGIME_SPY_SMA:
        return False
    sma = float(hist["close"].rolling(REGIME_SPY_SMA).mean().iloc[-1])
    last = float(hist["close"].iloc[-1])
    return last < sma


def run_backtest(start: date, end: date, top_n: int, universe_size: int | None) -> dict:
    # 1) Universe 로드
    with open(REPO_ROOT / "data" / "sp500_full.json") as f:
        universe = json.load(f)["tickers"]
    if universe_size:
        universe = universe[:universe_size]
    logger.info("Universe: %d tickers", len(universe))

    # 2) SPY for rebalance dates + benchmark + regime
    spy = get_bars("SPY", "2017-01-01", end.isoformat())
    if spy.empty:
        raise RuntimeError("SPY bars not available")

    # 3) Pre-fetch all universe bars (fail-soft)
    bars_dict: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    fetch_start = "2017-01-01"  # 252봉 + 여유 warmup
    fetch_end = end.isoformat()
    for i, sym in enumerate(universe):
        try:
            df = get_bars(sym, fetch_start, fetch_end)
            if df is None or df.empty or len(df) < 252:
                skipped.append(sym)
                continue
            bars_dict[sym] = df
        except Exception as exc:
            skipped.append(sym)
            logger.debug("Skip %s: %s", sym, exc)
        if (i + 1) % 50 == 0:
            logger.info("  fetched %d/%d (skipped %d)", i + 1, len(universe), len(skipped))
    logger.info("Bars loaded: %d  (skipped %d)", len(bars_dict), len(skipped))

    # 4) Rebalance dates
    rebalance_dates = _generate_rebalance_dates(start, end, spy.index)
    logger.info("Rebalance dates: %d (first %s, last %s)",
                len(rebalance_dates), rebalance_dates[0].date(), rebalance_dates[-1].date())

    # 5) Walk forward
    prev_holdings: list[str] = []
    monthly_records: list[dict] = []
    cumulative_equity = 1.0
    equity_curve: list[tuple[date, float]] = []
    sector_meta: dict[str, str] = {}  # placeholder for v2

    for rb_ts in rebalance_dates:
        # Regime gate
        defensive = _evaluate_regime(spy, rb_ts)

        # Evaluate all symbols
        candidates: list[dict] = []
        for sym, bars in bars_dict.items():
            ev = _evaluate_symbol(sym, bars, rb_ts)
            if ev is not None:
                candidates.append(ev)

        if defensive:
            # 신규 0, 기존 보유 유지 (단 백테스트에서는 단순화 — 기존 list 그대로 다음 달까지 보유)
            picks_symbols = list(prev_holdings)
            new_picks = 0
            picks_meta = []
        else:
            scored = _score_candidates(candidates)
            final = _apply_turnover_cap(scored, prev_holdings, top_n, TURNOVER_CAP_KEEP)
            picks_symbols = [c["symbol"] for c in final]
            new_picks = sum(1 for s in picks_symbols if s not in prev_holdings)
            picks_meta = final

        # Compute next-month return (entry next-day open, exit next rebalance day open)
        # 단순화: rebalance day open → 다음 rebalance day open
        try:
            cur_idx_pos = list(spy.index).index(rb_ts)
        except ValueError:
            continue

        next_rb_ts: pd.Timestamp | None = None
        for fut_ts in rebalance_dates:
            if fut_ts > rb_ts:
                next_rb_ts = fut_ts
                break

        if next_rb_ts is None:
            break  # 마지막 rebalance, exit 못함

        # Per-symbol return
        symbol_rets: list[float] = []
        for sym in picks_symbols:
            df = bars_dict.get(sym)
            if df is None:
                symbol_rets.append(0.0)
                continue
            entry_row = df[df.index >= rb_ts]
            exit_row = df[df.index >= next_rb_ts]
            if entry_row.empty or exit_row.empty:
                symbol_rets.append(0.0)
                continue
            entry_p = float(entry_row.iloc[0]["open"])
            exit_p = float(exit_row.iloc[0]["open"])
            if entry_p <= 0:
                symbol_rets.append(0.0)
                continue
            symbol_rets.append(exit_p / entry_p - 1.0)

        # equal-weighted portfolio return (현금 0 가정)
        if symbol_rets:
            port_ret = sum(symbol_rets) / len(symbol_rets)
        else:
            port_ret = 0.0  # defensive면 0 (현금 100%)

        # SPY benchmark return
        spy_entry = float(spy[spy.index >= rb_ts].iloc[0]["open"])
        spy_exit = float(spy[spy.index >= next_rb_ts].iloc[0]["open"])
        spy_ret = spy_exit / spy_entry - 1.0

        cumulative_equity *= (1 + port_ret)
        equity_curve.append((rb_ts.date(), cumulative_equity))

        monthly_records.append({
            "rb_date": rb_ts.date().isoformat(),
            "next_rb_date": next_rb_ts.date().isoformat(),
            "defensive": defensive,
            "picks": picks_symbols,
            "new_picks": new_picks,
            "kept": top_n - new_picks if not defensive else len(prev_holdings),
            "port_ret_pct": round(port_ret * 100, 3),
            "spy_ret_pct": round(spy_ret * 100, 3),
            "alpha_pct": round((port_ret - spy_ret) * 100, 3),
            "cumulative_equity": round(cumulative_equity, 4),
        })
        prev_holdings = picks_symbols

    # 6) Aggregate metrics
    if not monthly_records:
        return {"error": "no_records"}

    rets = [r["port_ret_pct"] / 100.0 for r in monthly_records]
    spy_rets = [r["spy_ret_pct"] / 100.0 for r in monthly_records]
    alphas = [r["alpha_pct"] / 100.0 for r in monthly_records]

    avg_monthly = mean(rets)
    std_monthly = stdev(rets) if len(rets) > 1 else 0.0
    sharpe_monthly = (avg_monthly / std_monthly) if std_monthly > 0 else 0.0
    sharpe_annual = sharpe_monthly * math.sqrt(12)

    avg_alpha_monthly = mean(alphas)
    avg_alpha_annual = avg_alpha_monthly * 12

    # MDD
    peak = equity_curve[0][1]
    mdd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        dd = (eq / peak - 1) * 100
        mdd = min(mdd, dd)

    # CAGR
    years = (rebalance_dates[-1] - rebalance_dates[0]).days / 365.25
    cagr = (cumulative_equity ** (1 / years) - 1) * 100 if years > 0 else 0.0

    # Turnover
    total_new = sum(r["new_picks"] for r in monthly_records if not r["defensive"])
    rebal_non_def = sum(1 for r in monthly_records if not r["defensive"])
    avg_turnover_pct = (total_new / (rebal_non_def * top_n) * 100) if rebal_non_def > 0 else 0.0

    # Win rate
    wins = sum(1 for r in rets if r > 0)
    win_alpha = sum(1 for a in alphas if a > 0)

    # Top/bottom months
    sorted_rec = sorted(monthly_records, key=lambda x: x["port_ret_pct"], reverse=True)
    summary = {
        "windows": {
            "start": rebalance_dates[0].date().isoformat(),
            "end": rebalance_dates[-1].date().isoformat(),
            "n_rebal": len(monthly_records),
        },
        "universe_size": len(bars_dict),
        "top_n": top_n,
        "metrics": {
            "cumulative_return_pct": round((cumulative_equity - 1) * 100, 2),
            "cagr_pct": round(cagr, 2),
            "sharpe_monthly": round(sharpe_monthly, 3),
            "sharpe_annual": round(sharpe_annual, 3),
            "avg_monthly_pct": round(avg_monthly * 100, 3),
            "std_monthly_pct": round(std_monthly * 100, 3),
            "win_rate": f"{wins}/{len(rets)} ({wins/len(rets)*100:.0f}%)",
            "win_alpha": f"{win_alpha}/{len(alphas)} ({win_alpha/len(alphas)*100:.0f}%)",
            "avg_alpha_monthly_pct": round(avg_alpha_monthly * 100, 3),
            "avg_alpha_annual_pct": round(avg_alpha_annual * 100, 2),
            "mdd_pct": round(mdd, 2),
            "avg_turnover_pct": round(avg_turnover_pct, 1),
            "defensive_months": sum(1 for r in monthly_records if r["defensive"]),
        },
        "verification": {
            "sharpe_target_0.8": sharpe_annual >= 0.8,
            "alpha_target_5pct": avg_alpha_annual >= 0.05,
            "mdd_target_25pct": mdd >= -25.0,
            "turnover_target_50pct": avg_turnover_pct <= 50.0,
        },
        "top_5_months": [
            {"date": r["rb_date"], "ret": r["port_ret_pct"], "picks": r["picks"][:5]}
            for r in sorted_rec[:5]
        ],
        "bottom_5_months": [
            {"date": r["rb_date"], "ret": r["port_ret_pct"], "picks": r["picks"][:5]}
            for r in sorted_rec[-5:]
        ],
        "first_5_records": monthly_records[:5],
        "last_5_records": monthly_records[-5:],
    }
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2018, 1, 1))
    ap.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2024, 12, 31))
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--universe-size", type=int, default=None, help="Subset for quick test")
    args = ap.parse_args()

    result = run_backtest(args.start, args.end, args.top, args.universe_size)
    print(json.dumps(result, indent=2, default=str))
