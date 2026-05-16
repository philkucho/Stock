"""Intraday ORB+VWAP+RVOL 4-pass 단타 전략 백테스트.

실 운영 코드(scripts/intraday_confirm.py)의 evaluate_orb + compute_entry_stop_target
함수를 그대로 사용 → backtest 결과가 live 동작과 1:1 일치.

데이터 소스: Alpaca Historical Market Data API (1m bars, 최대 60일+)

시뮬레이션 모델:
  - 09:45 ET 시점에 4-pass 평가
  - 통과 시 entry 가격에 즉시 체결 가정 (stop_limit BUY가 trigger됐다고 본 시점)
  - qty=50:50 2-tier 분할 (qty=100 고정 → 50/50)
  - 09:45~16:00 1m bars 순회하며 stop/t1/t2 hit 추적
  - 1차 t1 hit 후 잔여 stop을 entry(breakeven)로 raise (monitor 로직 시뮬)
  - stop이 t1보다 같은 분봉에 동시 hit → 보수적으로 stop 우선
  - 16:00 미청산 → 마지막 1m close로 청산

CLI:
    python -m backtests.run_intraday_orb \
        --symbols TXN,VRT,AMAT,NVDA,AVGO \
        --start 2026-03-15 --end 2026-05-14 \
        --qty 100
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import sqrt
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from signals.opening_range import compute_entry_stop_target, evaluate_orb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backtest.intraday_orb")


ET_TZ = "America/New_York"
ENTRY_OFFSET_DEFAULT = 0.05
RVOL_LOOKBACK_DAYS = 20  # opening_range 모듈과 동일


# ─────────── 데이터 fetch ───────────


def fetch_alpaca_1m(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Alpaca Historical로 1m bars fetch. UTC index 반환 (opening_range 모듈 호환)."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_API_SECRET"],
    )
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end + timedelta(days=1),
    )
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return df
    # MultiIndex (symbol, timestamp) → timestamp만
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index(level=0, drop=True)
    df.index = df.index.tz_convert("UTC")
    # opening_range는 lowercase columns 기대
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


# ─────────── 트레이드 시뮬 ───────────


@dataclass
class TradeResult:
    date: str
    symbol: str
    entry_price: float
    stop_price: float
    target_1r: float
    target_2r: float
    exit_time: str
    exit_reason: str  # 'stop' / 'target_1r_then_close' / 'target_2r' / 'time_close' / 'mixed'
    qty_total: int
    qty_at_t1: int
    qty_at_t2: int
    qty_at_stop: int
    qty_at_close: int
    realized_pnl_usd: float
    pct_return: float
    r_multiple: float
    # diagnostics: evaluate_orb 시점 metrics + 09:45 시점 price (entry보다 이미 위였는지)
    or_high: float = 0.0
    or_low: float = 0.0
    or_range_pct: float = 0.0
    session_vwap: float = 0.0
    intraday_rvol: float = 0.0
    price_at_0945: float = 0.0
    price_above_entry_at_0945: bool = False  # True면 라이브에선 stop_limit 미체결 가능성
    # 09:30~exit 1m bars (시각화용)
    bars_1m: list[dict] | None = None
    notes: str = ""


def simulate_intraday_trade(
    simulation_bars: pd.DataFrame,
    entry: float,
    stop: float,
    t1: float,
    t2: float,
    qty_total: int,
) -> tuple[str, str, dict]:
    """09:45 이후 1m bars로 trade 시뮬. 2-tier 부분청산.

    returns: (exit_time_iso, exit_reason, qtys_by_event_dict)

    경합 처리 (같은 분봉에 stop과 target 동시 hit 가능):
      - stop이 t1보다 우선 (보수적, gap-down 시뮬)
      - 1차 hit 후 잔여 stop은 entry(breakeven)로 raise
    """
    qty_1 = qty_total // 2
    qty_2 = qty_total - qty_1
    sold_at_t1 = sold_at_t2 = sold_at_stop = sold_at_close = 0
    cur_stop = stop
    t1_hit = False
    t2_hit = False
    exit_time = simulation_bars.index[-1].isoformat() if not simulation_bars.empty else ""
    exit_reason = "time_close"

    for ts, row in simulation_bars.iterrows():
        hi = float(row["high"])
        lo = float(row["low"])

        # 1) stop hit check (보수적 우선)
        if lo <= cur_stop:
            remaining = qty_total - sold_at_t1 - sold_at_t2
            if remaining > 0:
                sold_at_stop = remaining
            exit_time = ts.isoformat()
            if not t1_hit:
                exit_reason = "stop"
            elif not t2_hit:
                exit_reason = "t1_then_stop"
            else:
                exit_reason = "t2_then_stop"  # 불가능하지만 logical fall-through
            break

        # 2) t1 hit
        if not t1_hit and hi >= t1:
            t1_hit = True
            sold_at_t1 = qty_1
            cur_stop = entry  # breakeven raise (monitor 로직 시뮬)
            # 같은 분봉에 t2도 hit 가능
            if qty_2 > 0 and hi >= t2:
                t2_hit = True
                sold_at_t2 = qty_2
                exit_time = ts.isoformat()
                exit_reason = "t2_both_same_bar"
                break

        # 3) t2 hit
        elif t1_hit and not t2_hit and qty_2 > 0 and hi >= t2:
            t2_hit = True
            sold_at_t2 = qty_2
            exit_time = ts.isoformat()
            exit_reason = "t2"
            break

    # 4) 시간 마감 청산
    if sold_at_t1 + sold_at_t2 + sold_at_stop < qty_total and not simulation_bars.empty:
        close_price = float(simulation_bars["close"].iloc[-1])
        sold_at_close = qty_total - sold_at_t1 - sold_at_t2 - sold_at_stop

    return exit_time, exit_reason, {
        "sold_at_t1": sold_at_t1,
        "sold_at_t2": sold_at_t2,
        "sold_at_stop": sold_at_stop,
        "sold_at_close": sold_at_close,
        "t1_hit": t1_hit,
        "t2_hit": t2_hit,
        "final_stop_price": cur_stop,
    }


def compute_pnl(entry: float, stop: float, t1: float, t2: float, qtys: dict, close_price: float) -> tuple[float, float]:
    """sold_at_* 기준 실현 PnL + r_multiple 계산."""
    r = entry - stop
    pnl = (
        qtys["sold_at_t1"] * (t1 - entry)
        + qtys["sold_at_t2"] * (t2 - entry)
        + qtys["sold_at_stop"] * (qtys["final_stop_price"] - entry)
        + qtys["sold_at_close"] * (close_price - entry)
    )
    qty_total = sum(qtys[k] for k in ("sold_at_t1", "sold_at_t2", "sold_at_stop", "sold_at_close"))
    r_mult = (pnl / (qty_total * r)) if qty_total > 0 and r > 0 else 0.0
    return round(pnl, 2), round(r_mult, 3)


# ─────────── 백테스트 루프 ───────────


def trading_days(start: date, end: date) -> list[date]:
    """평일만 반환 (휴장일 미적용 — 단순화)."""
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def run_backtest(
    symbols: list[str],
    start: date,
    end: date,
    qty: int = 100,
    entry_offset: float = ENTRY_OFFSET_DEFAULT,
) -> dict:
    """주어진 기간/종목에 대해 ORB 백테스트 실행. 결과 dict 반환."""
    results: list[TradeResult] = []
    # funnel: 어느 게이트에서 cell이 걸러지는지 집계
    stage_counts: dict[str, int] = defaultdict(int)
    days = trading_days(start, end)
    n_cells = len(days) * len(symbols)
    logger.info("Backtest: %d days × %d symbols = %d cells", len(days), len(symbols), n_cells)

    # 1) 각 종목별로 전체 기간 fetch (RVOL을 위해 start-RVOL_LOOKBACK_DAYS 부터)
    fetch_start = start - timedelta(days=RVOL_LOOKBACK_DAYS + 10)  # 휴장일 여유
    bars_cache: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            bars_cache[sym] = fetch_alpaca_1m(sym, fetch_start, end)
            logger.info("  fetched %s: %d rows", sym, len(bars_cache[sym]))
        except Exception as exc:
            logger.warning("  fetch failed %s: %s", sym, exc)
            bars_cache[sym] = pd.DataFrame()

    # 2) 각 (day, sym) 평가
    et_orb_end = time(9, 45)
    et_close = time(16, 0)

    for d in days:
        for sym in symbols:
            stage_counts["cells_attempted"] += 1
            full_bars = bars_cache.get(sym)
            if full_bars is None or full_bars.empty:
                stage_counts["no_data_for_symbol"] += 1
                continue

            day_start_utc = pd.Timestamp(d, tz="UTC")
            day_end_utc = day_start_utc + pd.Timedelta(days=1)
            today_bars = full_bars[(full_bars.index >= day_start_utc) & (full_bars.index < day_end_utc)]
            hist_start = day_start_utc - pd.Timedelta(days=RVOL_LOOKBACK_DAYS + 5)
            hist_bars = full_bars[(full_bars.index >= hist_start) & (full_bars.index < day_start_utc)]

            if today_bars.empty or len(today_bars) < 16:
                stage_counts["insufficient_bars_today"] += 1
                continue

            try:
                evaluation = evaluate_orb(sym, today_bars, hist_bars, d)
            except Exception as exc:
                logger.debug("eval failed %s %s: %s", d, sym, exc)
                stage_counts["eval_exception"] += 1
                continue
            if evaluation is None:
                stage_counts["eval_returned_none"] += 1
                continue

            # 게이트별 fail 카운트 (서로 독립적으로 집계 — 합산 ≠ all_fail)
            if not evaluation.pass_orb:
                stage_counts["fail_orb_break"] += 1
            if not evaluation.pass_vwap:
                stage_counts["fail_vwap"] += 1
            if not evaluation.pass_rvol:
                stage_counts["fail_rvol"] += 1
            if not evaluation.pass_range:
                stage_counts["fail_range"] += 1

            if not evaluation.all_passed:
                stage_counts["any_gate_failed"] += 1
                continue

            stage_counts["all_4_gates_passed"] += 1

            levels = compute_entry_stop_target(evaluation, entry_offset=entry_offset)
            if levels is None:
                stage_counts["levels_rejected"] += 1
                continue
            entry, stop, t1, t2 = levels

            sim_start = pd.Timestamp(
                f"{d.isoformat()} {et_orb_end.strftime('%H:%M')}", tz=ET_TZ,
            ).tz_convert("UTC")
            sim_end = pd.Timestamp(
                f"{d.isoformat()} {et_close.strftime('%H:%M')}", tz=ET_TZ,
            ).tz_convert("UTC")
            simulation_bars = today_bars[(today_bars.index >= sim_start) & (today_bars.index <= sim_end)]
            if simulation_bars.empty:
                stage_counts["no_sim_bars"] += 1
                continue

            stage_counts["traded"] += 1

            exit_time, exit_reason, qtys = simulate_intraday_trade(
                simulation_bars, entry, stop, t1, t2, qty,
            )
            close_price = float(simulation_bars["close"].iloc[-1])
            pnl, r_mult = compute_pnl(entry, stop, t1, t2, qtys, close_price)
            pct = round((pnl / (qty * entry)) * 100, 3) if entry > 0 else 0.0

            # diagnostics: 09:30~exit 1m bars 덤프 + price_at_0945 (entry보다 위였는지 체크)
            day_start_et = pd.Timestamp(f"{d.isoformat()} 09:30", tz=ET_TZ).tz_convert("UTC")
            exit_ts = pd.Timestamp(exit_time)
            bars_for_dump = today_bars[(today_bars.index >= day_start_et) & (today_bars.index <= exit_ts)]
            bars_payload = [
                {
                    "ts": ts.isoformat(),
                    "o": round(float(r["open"]), 4),
                    "h": round(float(r["high"]), 4),
                    "l": round(float(r["low"]), 4),
                    "c": round(float(r["close"]), 4),
                    "v": int(r["volume"]),
                }
                for ts, r in bars_for_dump.iterrows()
            ]
            price_at_0945 = float(simulation_bars["open"].iloc[0]) if not simulation_bars.empty else 0.0

            results.append(TradeResult(
                date=d.isoformat(),
                symbol=sym,
                entry_price=round(entry, 2),
                stop_price=round(stop, 2),
                target_1r=round(t1, 2),
                target_2r=round(t2, 2),
                exit_time=exit_time,
                exit_reason=exit_reason,
                qty_total=qty,
                qty_at_t1=qtys["sold_at_t1"],
                qty_at_t2=qtys["sold_at_t2"],
                qty_at_stop=qtys["sold_at_stop"],
                qty_at_close=qtys["sold_at_close"],
                realized_pnl_usd=pnl,
                pct_return=pct,
                r_multiple=r_mult,
                or_high=round(evaluation.or_high, 4),
                or_low=round(evaluation.or_low, 4),
                or_range_pct=round(evaluation.or_range_pct, 5),
                session_vwap=round(evaluation.session_vwap, 4),
                intraday_rvol=round(evaluation.intraday_rvol, 3),
                price_at_0945=round(price_at_0945, 4),
                price_above_entry_at_0945=price_at_0945 > entry,
                bars_1m=bars_payload,
            ))

    summary = summarize(results)
    summary["stage_funnel"] = dict(stage_counts)
    summary["n_cells_total"] = n_cells
    return summary


# ─────────── 요약 KPI ───────────


def summarize(trades: list[TradeResult]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "trades": []}

    pnls = [t.realized_pnl_usd for t in trades]
    rets = [t.pct_return for t in trades]
    r_mults = [t.r_multiple for t in trades]

    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / n

    avg_ret = sum(rets) / n
    if n >= 2:
        var = sum((r - avg_ret) ** 2 for r in rets) / (n - 1)
        std = var ** 0.5
        sharpe = (avg_ret / std) * sqrt(252) if std > 0 else 0.0
    else:
        sharpe = 0.0

    # max drawdown (누적 PnL 기준)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)

    by_reason = defaultdict(int)
    for t in trades:
        by_reason[t.exit_reason] += 1

    by_symbol = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in trades:
        s = by_symbol[t.symbol]
        s["n"] += 1
        s["pnl"] += t.realized_pnl_usd

    return {
        "n_trades": n,
        "win_rate": round(win_rate, 3),
        "avg_return_pct": round(avg_ret, 3),
        "avg_r_multiple": round(sum(r_mults) / n, 3),
        "sharpe_annualized": round(sharpe, 2),
        "total_pnl_usd": round(total_pnl, 2),
        "max_drawdown_usd": round(max_dd, 2),
        "win_count": len(wins),
        "loss_count": len(losses),
        "avg_win_usd": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_usd": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "exit_reasons": dict(by_reason),
        "by_symbol": {sym: {"n": v["n"], "pnl": round(v["pnl"], 2)} for sym, v in by_symbol.items()},
        "trades": [asdict(t) for t in trades],
    }


# ─────────── CLI ───────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", required=True, help="콤마 구분 (예: TXN,VRT,AMAT)")
    p.add_argument("--start", required=True, type=lambda s: date.fromisoformat(s))
    p.add_argument("--end", required=True, type=lambda s: date.fromisoformat(s))
    p.add_argument("--qty", type=int, default=100)
    p.add_argument("--entry-offset", type=float, default=ENTRY_OFFSET_DEFAULT)
    p.add_argument("--output", type=Path, default=None, help="JSON 결과 저장 경로")
    p.add_argument("--max-trades-stdout", type=int, default=20)
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    summary = run_backtest(symbols, args.start, args.end, qty=args.qty, entry_offset=args.entry_offset)

    # stdout: 요약만, trades는 head N (단, 1m bars 덤프는 stdout에서 제외 — 너무 큼)
    short_summary = {k: v for k, v in summary.items() if k != "trades"}
    sample = []
    for t in summary.get("trades", [])[: args.max_trades_stdout]:
        slim = {k: v for k, v in t.items() if k != "bars_1m"}
        slim["bars_1m_count"] = len(t.get("bars_1m") or [])
        sample.append(slim)
    short_summary["sample_trades"] = sample
    print(json.dumps(short_summary, indent=2, default=str))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info("Full results saved to %s", args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
