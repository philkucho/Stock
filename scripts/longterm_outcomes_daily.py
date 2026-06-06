"""LongtermPick의 horizon별 outcome 일일 백필.

각 LongtermPick에 대해 21/63/126/252 영업일 경과 시 alpha 적재.
멱등성: (pick_id, days_held) UNIQUE — 이미 있으면 skip.

CLI:
    venv/Scripts/python.exe -m scripts.longterm_outcomes_daily
    venv/Scripts/python.exe -m scripts.longterm_outcomes_daily --eval-date 2026-06-30
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "longterm_outcomes"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"{date.today().isoformat()}.log", encoding="utf-8"
        ),
    ],
)

logger = logging.getLogger("longterm_outcomes")

HORIZONS = [21, 63, 126, 252]


def _bars_at_or_after(bars: pd.DataFrame, target_ts: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    """target 이후 첫 거래일 (ts, open price) 반환."""
    fwd = bars[bars.index >= target_ts]
    if fwd.empty:
        return None
    return fwd.index[0], float(fwd.iloc[0]["open"])


def _trading_day_after_n(bars: pd.DataFrame, start_ts: pd.Timestamp, n: int) -> pd.Timestamp | None:
    """start_ts (포함) 부터 n번째 거래일."""
    fwd_idx = bars.index[bars.index >= start_ts]
    if len(fwd_idx) <= n:
        return None
    return fwd_idx[n]


async def main(eval_date: date) -> dict:
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from api.db.models import LongtermOutcome, LongtermPick
    from api.db.session import async_session_factory
    from backtests.data_cache import get_bars

    eval_ts = pd.Timestamp(eval_date, tz="UTC")
    out: dict = {"eval_date": eval_date.isoformat(), "created": 0, "skipped": 0, "errors": 0}

    async with async_session_factory() as s:
        stmt = select(LongtermPick).where(
            LongtermPick.status.in_(["new", "hold", "exit_suggested", "exited"])
        )
        picks = list((await s.execute(stmt)).scalars().all())

    if not picks:
        logger.info("no picks to evaluate")
        return out

    logger.info("evaluating %d picks for eval_date=%s", len(picks), eval_date)

    spy_bars = get_bars("SPY", "2017-01-01", eval_date.isoformat())
    if spy_bars.empty:
        logger.error("SPY bars unavailable")
        out["errors"] = 1
        return out

    # 멱등성 — 이미 적재된 (pick_id, days_held) 조회
    async with async_session_factory() as s:
        existing_stmt = select(
            LongtermOutcome.pick_id, LongtermOutcome.days_held
        )
        existing = {(r[0], r[1]) for r in (await s.execute(existing_stmt)).all()}

    for pick in picks:
        try:
            symbol_bars = get_bars(
                pick.symbol, "2017-01-01", eval_date.isoformat()
            )
            if symbol_bars.empty:
                logger.debug("no bars for %s", pick.symbol)
                continue

            entry_ts_target = pd.Timestamp(pick.pick_month, tz="UTC")
            entry = _bars_at_or_after(symbol_bars, entry_ts_target)
            spy_entry = _bars_at_or_after(spy_bars, entry_ts_target)
            if entry is None or spy_entry is None:
                continue
            entry_ts, entry_p = entry
            spy_entry_ts, spy_entry_p = spy_entry

            for h in HORIZONS:
                key = (pick.id, h)
                if key in existing:
                    out["skipped"] += 1
                    continue

                exit_ts = _trading_day_after_n(symbol_bars, entry_ts, h)
                if exit_ts is None or exit_ts > eval_ts:
                    # 아직 horizon 미도래
                    continue
                spy_exit_ts = _trading_day_after_n(spy_bars, spy_entry_ts, h)
                if spy_exit_ts is None:
                    continue

                exit_p = float(symbol_bars.loc[exit_ts]["close"])
                spy_exit_p = float(spy_bars.loc[spy_exit_ts]["close"])
                if entry_p <= 0 or spy_entry_p <= 0:
                    continue
                pct_ret = (exit_p / entry_p - 1.0) * 100
                spy_ret = (spy_exit_p / spy_entry_p - 1.0) * 100
                alpha_v = pct_ret - spy_ret

                # MFE/MAE — entry_ts ~ exit_ts 윈도우 내 최고/최저
                window = symbol_bars.loc[entry_ts:exit_ts]
                if not window.empty:
                    mfe = (float(window["high"].max()) / entry_p - 1.0) * 100
                    mae = (float(window["low"].min()) / entry_p - 1.0) * 100
                else:
                    mfe = mae = 0.0

                row = {
                    "pick_id": pick.id,
                    "eval_date": exit_ts.date(),
                    "days_held": h,
                    "pct_return": Decimal(f"{pct_ret:.4f}"),
                    "spy_pct_return": Decimal(f"{spy_ret:.4f}"),
                    "alpha": Decimal(f"{alpha_v:.4f}"),
                    "mfe_pct": Decimal(f"{mfe:.4f}"),
                    "mae_pct": Decimal(f"{mae:.4f}"),
                    "status_at_eval": pick.status,
                }
                async with async_session_factory() as s2:
                    stmt = pg_insert(LongtermOutcome).values(**row)
                    stmt = stmt.on_conflict_do_nothing(
                        constraint="uq_longterm_outcome_pick_horizon"
                    )
                    await s2.execute(stmt)
                    await s2.commit()
                out["created"] += 1
        except Exception as exc:
            logger.exception("outcome fail for %s pick_id=%d", pick.symbol, pick.id)
            out["errors"] += 1

    logger.info(
        "outcomes: created=%d skipped=%d errors=%d",
        out["created"], out["skipped"], out["errors"],
    )
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-date", type=lambda s: date.fromisoformat(s), default=None)
    args = ap.parse_args()
    eval_d = args.eval_date or date.today()
    result = asyncio.run(main(eval_d))
    print(result)
