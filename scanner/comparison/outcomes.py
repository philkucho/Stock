"""다중 지평 결과 백필 — 매일 16:30 ET 후 자동 실행.

각 system_pick_log에 대해 1d/5d/10d 윈도우의 실현 수익 + SPY 알파 계산.

- entry_price: pick_date 다음 거래일의 시초가 (보유 시작)
- exit_price: entry_date + horizon_days 거래일의 종가
- pct_return = (exit/entry − 1) × 100
- spy_pct_return = (SPY exit/SPY entry − 1) × 100
- alpha = pct_return − spy_pct_return
- win_simple = pct_return > 0
- win_alpha = alpha > 0
- realized_pnl_usd = sim_capital_usd × pct_return / 100
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import PickOutcome, SystemPickLog, TradePlan, TradePlanOutcome
from backtests.data_cache import get_bars, refresh_cache
from scanner.comparison import BENCHMARK_SYMBOL, HOLDING_HORIZONS

logger = logging.getLogger(__name__)


@dataclass
class _BarRow:
    open: float
    high: float
    low: float
    close: float


def _next_trading_open(bars: pd.DataFrame, after_date: date) -> tuple[date, float] | None:
    """after_date 직후의 첫 거래일 시초가."""
    after_ts = pd.Timestamp(after_date, tz="UTC")
    future = bars[bars.index > after_ts]
    if future.empty:
        return None
    first = future.iloc[0]
    return future.index[0].date(), float(first["open"])


def _close_at_or_after(bars: pd.DataFrame, target_date: date, target_idx_offset: int) -> tuple[date, float] | None:
    """entry 기준 N 거래일 후 종가."""
    after_ts = pd.Timestamp(target_date, tz="UTC")
    future = bars[bars.index >= after_ts]
    if len(future) < target_idx_offset + 1:
        return None
    row = future.iloc[target_idx_offset]
    return future.index[target_idx_offset].date(), float(row["close"])


async def backfill_pick_outcomes(
    session: AsyncSession,
    target_date: date | None = None,
    *,
    lookback_days: int = 30,
) -> dict:
    """target_date 기준 lookback_days 이전부터 outcomes를 채우려 시도.

    - target_date 디폴트: 오늘
    - lookback_days: 그 이전 N일까지 outcomes 누락분 백필 (기본 30일 — 10d horizon 여유)
    """
    if target_date is None:
        target_date = date.today()
    cutoff = target_date - timedelta(days=lookback_days)

    end_iso = target_date.isoformat()
    start_iso = (cutoff - timedelta(days=20)).isoformat()

    # 백필 대상 picks: 아직 outcome 없거나 부분만 있는 것
    stmt = (
        select(SystemPickLog)
        .where(SystemPickLog.pick_date >= cutoff)
        .where(SystemPickLog.pick_date <= target_date)
    )
    result = await session.execute(stmt)
    pick_logs = list(result.scalars().all())
    logger.info("Backfill: %d pick logs to evaluate", len(pick_logs))

    # data_cache.get_bars는 stale-detection 없이 캐시 우선 반환 — picks 심볼 + SPY를 사전 갱신해야
    # 마지막 cron 이후 새로 마감된 일봉을 끌어올 수 있다.
    symbols_to_refresh = sorted({pl.symbol for pl in pick_logs} | {BENCHMARK_SYMBOL})
    refresh_results = refresh_cache(symbols_to_refresh, "1d")
    refresh_errors = {s: msg for s, msg in refresh_results.items() if msg != "ok"}
    if refresh_errors:
        logger.warning("Bars refresh errors (%d): %s", len(refresh_errors), refresh_errors)

    spy_bars = get_bars(BENCHMARK_SYMBOL, start_iso, end_iso, "1d")
    if spy_bars.empty:
        logger.warning("SPY bars empty — cannot compute alpha")
        return {"error": "spy_bars_empty"}

    # 기존 outcomes 조회 (중복 방지용)
    existing_stmt = select(PickOutcome.pick_log_id, PickOutcome.horizon_days)
    existing_rows = (await session.execute(existing_stmt)).all()
    existing_set = {(r[0], r[1]) for r in existing_rows}

    summary = {"created": 0, "skipped": 0, "errors": 0}

    for pl in pick_logs:
        try:
            sym_bars = get_bars(
                pl.symbol,
                start_iso,
                end_iso,
                "1d",
            )
            if sym_bars.empty:
                summary["errors"] += 1
                continue

            # entry: pick_date 다음 거래일 시초가
            entry_pair = _next_trading_open(sym_bars, pl.pick_date)
            spy_entry_pair = _next_trading_open(spy_bars, pl.pick_date)
            if not entry_pair or not spy_entry_pair:
                summary["skipped"] += 1
                continue
            entry_date, entry_price = entry_pair
            spy_entry_date, spy_entry_price = spy_entry_pair

            # entry_price를 SystemPickLog에도 채움 (한 번만)
            if pl.entry_price is None:
                pl.entry_price = Decimal(f"{entry_price:.4f}")

            # 각 지평
            for horizon in HOLDING_HORIZONS:
                if (pl.id, horizon) in existing_set:
                    continue
                exit_pair = _close_at_or_after(sym_bars, entry_date, horizon)
                spy_exit_pair = _close_at_or_after(spy_bars, spy_entry_date, horizon)
                if not exit_pair or not spy_exit_pair:
                    # 아직 데이터 부족 — 다음 백필에서 채움
                    continue
                exit_date, exit_price = exit_pair
                _, spy_exit_price = spy_exit_pair

                pct = (exit_price / entry_price - 1.0) * 100.0
                spy_pct = (spy_exit_price / spy_entry_price - 1.0) * 100.0
                alpha = pct - spy_pct
                pnl_usd = float(pl.sim_capital_usd) * pct / 100.0

                outcome = PickOutcome(
                    pick_log_id=pl.id,
                    horizon_days=horizon,
                    exit_date=exit_date,
                    exit_price=Decimal(f"{exit_price:.4f}"),
                    pct_return=Decimal(f"{pct:.4f}"),
                    spy_pct_return=Decimal(f"{spy_pct:.4f}"),
                    alpha=Decimal(f"{alpha:.4f}"),
                    win_simple=pct > 0,
                    win_alpha=alpha > 0,
                    realized_pnl_usd=Decimal(f"{pnl_usd:.2f}"),
                )
                session.add(outcome)
                summary["created"] += 1
        except Exception as exc:
            logger.warning("backfill error for log_id=%s sym=%s: %s", pl.id, pl.symbol, exc)
            summary["errors"] += 1

    await session.commit()
    logger.info("Backfill summary: %s", summary)
    return summary


# ─────────── Trade Plan outcomes (사용자 입력 매매 plan 추적) ───────────


def _high_low_within(bars: pd.DataFrame, start_date: date, n_bars: int) -> tuple[float, float] | None:
    """start_date부터 n_bars 거래일의 고점/저점 (target/stop hit 판정용)."""
    after_ts = pd.Timestamp(start_date, tz="UTC")
    future = bars[bars.index >= after_ts]
    if len(future) < 1:
        return None
    window = future.iloc[: max(1, n_bars + 1)]  # entry 포함 n+1봉
    return float(window["high"].max()), float(window["low"].min())


async def backfill_trade_plan_outcomes(
    session: AsyncSession,
    target_date: date | None = None,
    *,
    lookback_days: int = 30,
) -> dict:
    """trade_plans의 1d/5d/10d 실현 수익 + 손익 백필.

    pick_outcomes와 다른 점:
      - shares × (exit − entry) = 사용자 실현 손익 ($)
      - hit_target_1r / hit_stop: 윈도우 내 도달 여부
      - entry_price = 사용자 저장 시점의 entry (pivot 가격)
    """
    if target_date is None:
        target_date = date.today()
    cutoff = target_date - timedelta(days=lookback_days)

    end_iso = target_date.isoformat()
    start_iso = (cutoff - timedelta(days=20)).isoformat()

    stmt = select(TradePlan).where(
        TradePlan.plan_date >= cutoff,
        TradePlan.plan_date <= target_date,
    )
    plans = list((await session.execute(stmt)).scalars().all())
    logger.info("TradePlan backfill: %d plans to evaluate", len(plans))

    # 캐시 stale 방지 — pick outcomes 백필과 동일 이유
    symbols_to_refresh = sorted({p.symbol for p in plans} | {BENCHMARK_SYMBOL})
    refresh_results = refresh_cache(symbols_to_refresh, "1d")
    refresh_errors = {s: msg for s, msg in refresh_results.items() if msg != "ok"}
    if refresh_errors:
        logger.warning("TradePlan bars refresh errors (%d): %s", len(refresh_errors), refresh_errors)

    spy_bars = get_bars(BENCHMARK_SYMBOL, start_iso, end_iso, "1d")
    if spy_bars.empty:
        return {"error": "spy_bars_empty"}

    existing_stmt = select(TradePlanOutcome.trade_plan_id, TradePlanOutcome.horizon_days)
    existing = {(r[0], r[1]) for r in (await session.execute(existing_stmt)).all()}

    summary = {"created": 0, "skipped": 0, "errors": 0}

    for pl in plans:
        try:
            sym_bars = get_bars(pl.symbol, start_iso, end_iso, "1d")
            if sym_bars.empty:
                summary["errors"] += 1
                continue

            # entry: pl.plan_date 다음 거래일 시초가 — 사용자가 그 시가에 매수했다고 가정
            entry_pair = _next_trading_open(sym_bars, pl.plan_date)
            spy_entry_pair = _next_trading_open(spy_bars, pl.plan_date)
            if not entry_pair or not spy_entry_pair:
                summary["skipped"] += 1
                continue
            entry_date, actual_entry = entry_pair
            spy_entry_date, spy_entry_price = spy_entry_pair

            # 사용자 저장 entry_price와 actual_entry가 다를 수 있음 → actual 사용 (실제 시장가 시뮬)
            shares = int(pl.shares)
            target_1r = float(pl.target_1r)
            target_2r = float(pl.target_2r)
            stop_price = float(pl.stop_price)
            # 2-tier qty 분할 (qty=1은 1차만 = single bracket fallback)
            from broker_adapter.base import qty_split_50_50
            qty_1, qty_2 = qty_split_50_50(shares)
            is_two_tier = qty_2 > 0

            for horizon in HOLDING_HORIZONS:
                if (pl.id, horizon) in existing:
                    continue

                exit_pair = _close_at_or_after(sym_bars, entry_date, horizon)
                spy_exit_pair = _close_at_or_after(spy_bars, spy_entry_date, horizon)
                if not exit_pair or not spy_exit_pair:
                    continue
                exit_date, exit_price = exit_pair
                _, spy_exit_price = spy_exit_pair

                # 시간순 hit 검증 (day-by-day) — 2-tier 부분 청산 시뮬에 필수
                hit_t1 = hit_t2 = hit_stop = False
                t1_day = t2_day = stop_day = None
                # entry_date는 date, sym_bars.index는 Timestamp — date끼리 비교
                window_idx = sorted(sym_bars.index)
                try:
                    start_i = next(
                        i for i, ts in enumerate(window_idx) if ts.date() == entry_date
                    )
                except StopIteration:
                    summary["errors"] += 1
                    continue
                hold_idx = window_idx[start_i:start_i + horizon + 1]
                for i, d in enumerate(hold_idx):
                    row = sym_bars.loc[d]
                    hi, lo = float(row["high"]), float(row["low"])
                    if not hit_stop and lo <= stop_price:
                        hit_stop = True; stop_day = i
                    if not hit_t1 and hi >= target_1r:
                        hit_t1 = True; t1_day = i
                    if is_two_tier and not hit_t2 and hi >= target_2r:
                        hit_t2 = True; t2_day = i

                # 부분 청산 시뮬 (qty_1 at t1, qty_2 at t2 / stop / horizon close)
                # 우선순위: stop이 t1 전이면 전량 stop, 그 외엔 t1 도달분 청산 후 잔여 추적
                if hit_stop and (not hit_t1 or stop_day < t1_day):
                    # stop 먼저 → 전량 stop 청산
                    qty_sold_at_1r = 0
                    qty_sold_at_2r = 0  # 잔여
                    pnl_partial = shares * (stop_price - actual_entry)
                elif is_two_tier and hit_t1 and hit_t2 and (not hit_stop or t2_day < stop_day):
                    # 1차+2차 모두 도달 (stop 전)
                    qty_sold_at_1r = qty_1
                    qty_sold_at_2r = qty_2
                    pnl_partial = qty_1 * (target_1r - actual_entry) + qty_2 * (target_2r - actual_entry)
                elif is_two_tier and hit_t1 and hit_stop and t1_day <= stop_day and (not hit_t2 or stop_day < t2_day):
                    # 1차 도달 후 2차 미도달, 잔여 stop hit
                    qty_sold_at_1r = qty_1
                    qty_sold_at_2r = qty_2  # stop으로 청산
                    pnl_partial = qty_1 * (target_1r - actual_entry) + qty_2 * (stop_price - actual_entry)
                elif hit_t1 and not is_two_tier:
                    # qty=1 fallback: 1차에서 전량 청산
                    qty_sold_at_1r = qty_1
                    qty_sold_at_2r = 0
                    pnl_partial = qty_1 * (target_1r - actual_entry)
                elif hit_t1 and is_two_tier and not hit_t2 and not hit_stop:
                    # 1차 도달, 2차 미도달, stop 미hit → 잔여는 horizon close 청산
                    qty_sold_at_1r = qty_1
                    qty_sold_at_2r = qty_2  # close 시점
                    pnl_partial = qty_1 * (target_1r - actual_entry) + qty_2 * (exit_price - actual_entry)
                else:
                    # 1차 미도달, stop 미hit → 전량 horizon close
                    qty_sold_at_1r = 0
                    qty_sold_at_2r = 0
                    pnl_partial = shares * (exit_price - actual_entry)

                pct = (exit_price / actual_entry - 1.0) * 100.0
                spy_pct = (spy_exit_price / spy_entry_price - 1.0) * 100.0
                alpha = pct - spy_pct
                # 단순 실손익 (1-tier 호환: shares × (exit − entry))
                realized_pnl = shares * (exit_price - actual_entry)

                outcome = TradePlanOutcome(
                    trade_plan_id=pl.id,
                    horizon_days=horizon,
                    exit_date=exit_date,
                    exit_price=Decimal(f"{exit_price:.4f}"),
                    pct_return=Decimal(f"{pct:.4f}"),
                    spy_pct_return=Decimal(f"{spy_pct:.4f}"),
                    alpha=Decimal(f"{alpha:.4f}"),
                    realized_pnl_usd=Decimal(f"{realized_pnl:.2f}"),
                    hit_target_1r=hit_t1,
                    hit_target_2r=hit_t2,
                    hit_stop=hit_stop,
                    qty_sold_at_1r=qty_sold_at_1r,
                    qty_sold_at_2r=qty_sold_at_2r,
                    partial_realized_pnl_usd=Decimal(f"{pnl_partial:.2f}"),
                )
                session.add(outcome)
                summary["created"] += 1
        except Exception as exc:
            logger.warning("trade plan backfill error id=%s sym=%s: %s", pl.id, pl.symbol, exc)
            summary["errors"] += 1

    await session.commit()
    logger.info("TradePlan backfill summary: %s", summary)
    return summary
