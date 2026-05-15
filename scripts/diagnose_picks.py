"""Stage 2 진단 — 각 universe 멤버가 어느 gate에서 fail하는지 출력."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import UniverseMember
from api.db.session import async_session_factory
from scanner.catalysts import aggregate_catalyst
from scanner.stage2_daily_picks import (
    SCORE_THRESHOLD,
    evaluate_gates,
    evaluate_scores,
    fetch_candidate_metrics,
    get_market_context,
)


async def diagnose(after_hours_lenient: bool = True) -> None:
    target = date.today()
    market_ctx = get_market_context(target)
    print(f"after_hours_lenient = {after_hours_lenient}")
    print(f"Market context (QQQ gap = {market_ctx.get('QQQ_gap_pct', '?'):.2f}%):")
    for k, v in market_ctx.items():
        print(f"  {k:20s} {v:+.2f}%")

    async with async_session_factory() as session:
        result = await session.execute(
            select(UniverseMember).where(
                UniverseMember.enabled == True,
                UniverseMember.source != "blacklist",
            )
        )
        members = list(result.scalars().all())
    symbols = sorted({m.symbol for m in members})
    print(f"\nUniverse: {len(symbols)} distinct symbols\n")

    pass_count = 0
    fail_counts = {f"g{i}_{name}": 0 for i, name in enumerate(
        ["market", "dollar_vol", "momentum", "spread", "catalyst", "traps"], 1
    )}

    print(
        f"{'symbol':6s} {'gap%':>7s} {'rvol':>6s} {'pmDV($M)':>9s} "
        f"{'spread':>7s} {'cat':>4s} {'flt(M)':>7s} {'gates':>8s} {'score':>6s}"
    )
    print("-" * 90)
    score_pass_count = 0
    whitelist_syms = {m.symbol for m in members if m.source == "score5_whitelist"}
    for sym in symbols:
        m = fetch_candidate_metrics(sym, target)
        cat = aggregate_catalyst(sym, target)
        gate = evaluate_gates(
            m, cat, market_ctx, target, after_hours_lenient=after_hours_lenient
        )
        gd = asdict(gate)
        fail_summary = "".join("." if v else "X" for v in gd.values())
        if gate.all_passed():
            pass_count += 1
        else:
            for k, v in zip(fail_counts.keys(), gd.values()):
                if not v:
                    fail_counts[k] += 1
        score, _ = evaluate_scores(
            m, cat, market_ctx, sym in whitelist_syms, daily_bars=None, intraday_bars=None
        )
        if gate.all_passed() and score.total >= SCORE_THRESHOLD:
            score_pass_count += 1
        flt = (m.float_shares / 1e6) if m.float_shares else 0
        pmdv_m = m.premarket_dollar_vol / 1e6
        spread = m.spread_pct if m.spread_pct is not None else 0
        print(
            f"{sym:6s} {m.gap_pct:+7.2f} {m.rvol:6.2f} {pmdv_m:9.2f} "
            f"{spread:7.3f} {cat.score:4d} {flt:7.0f} {fail_summary:>8s} {score.total:6.2f}"
        )

    print(f"\nPassed all gates: {pass_count} / {len(symbols)}")
    print(f"Passed gates AND score >= {SCORE_THRESHOLD}: {score_pass_count} / {len(symbols)}")
    print("\nGate failure counts (lower is better):")
    for k, v in fail_counts.items():
        print(f"  {k:25s} {v:3d}")


if __name__ == "__main__":
    asyncio.run(diagnose())
