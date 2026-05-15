"""각 시스템에서 daily picks를 동일 스키마로 추출.

각 어댑터는 `(system_id, date) -> list[PickCandidate]` 인터페이스 통일.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import DailyPick

logger = logging.getLogger(__name__)


@dataclass
class PickCandidate:
    """3 시스템 통합 표준 picks 형식."""
    system_id: str
    rank: int
    symbol: str
    score: float
    score_meta: dict[str, Any] = field(default_factory=dict)
    sector: str | None = None
    strategy_tag: str = "swing"


# ─────────── v3 Daily Picks ───────────


async def fetch_v3_picks(session: AsyncSession, target_date: date, top: int = 5) -> list[PickCandidate]:
    """v3 시스템 picks. 우선순위:
       1) daily_picks 테이블에 target_date 데이터가 있으면 그것 사용
       2) 없으면 historical 재계산 (과거 일봉 기반, lenient 모드)
    """
    stmt = (
        select(DailyPick)
        .where(DailyPick.pick_date == target_date)
        .order_by(DailyPick.rank)
        .limit(top)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    if rows:
        out: list[PickCandidate] = []
        for r in rows:
            out.append(
                PickCandidate(
                    system_id="v3",
                    rank=r.rank,
                    symbol=r.symbol,
                    score=float(r.total_score),
                    score_meta={
                        "score_breakdown": r.score_breakdown,
                        "is_backup": r.is_backup,
                        "pivot_price": float(r.pivot_price),
                        "stop_price": float(r.stop_price),
                        "target_1r": float(r.target_1r),
                        "target_2r": float(r.target_2r),
                    },
                    sector=r.sector,
                    strategy_tag=r.strategy_tag or "swing",
                )
            )
        return out

    # daily_picks에 없음 — historical 재계산 (과거 날짜 backfill용)
    from datetime import date as _date

    if target_date < _date.today():
        try:
            from scanner.comparison.v3_historical import run_v3_for_date
            return await run_v3_for_date(session, target_date, top)
        except Exception as exc:
            logger.warning("v3 historical eval failed for %s: %s", target_date, exc)
            return []

    return []


# ─────────── scan_momentum (사용자의 검증된 시스템) ───────────


async def fetch_scanner_picks(target_date: date | None = None, top: int = 5) -> list[PickCandidate]:
    """scan_momentum 파이프라인 직접 호출하여 top N picks 추출.

    api/routes/scanner.py의 get_today() 로직과 동일하게 평가한 후 score≥4 + earnings pre 차단.
    """
    from pathlib import Path

    from scripts.scan_momentum import (
        EARNINGS_CALENDAR_PATH,
        earnings_phase,
        evaluate_at_date,
        fetch_bars,
        list_symbols,
        load_earnings_calendar,
    )

    project_root = Path(__file__).resolve().parent.parent.parent

    # WHITELIST + sector map 로드
    whitelist_path = project_root / "data" / "symbol_filter_v3_sp500.json"
    sector_path = project_root / "data" / "sector_map.json"

    sectors: dict[str, str] = {}
    if sector_path.exists():
        import json as _json
        sectors = _json.loads(sector_path.read_text(encoding="utf-8"))

    earnings_data = None
    try:
        earnings_data = load_earnings_calendar(EARNINGS_CALENDAR_PATH)
    except Exception as exc:
        logger.debug("scan_momentum earnings calendar load failed: %s", exc)

    # 종목별 평가
    try:
        symbols = await list_symbols()
    except Exception as exc:
        logger.warning("scan_momentum list_symbols failed: %s", exc)
        return []

    candidates: list[dict] = []
    for sym in symbols:
        try:
            bars = await fetch_bars(sym)
            result = evaluate_at_date(bars, target_date)
            if result is None:
                continue
            result["symbol"] = sym
            result["sector"] = sectors.get(sym)
            # earnings phase
            if earnings_data is not None:
                try:
                    phase = earnings_phase(
                        sym,
                        date.fromisoformat(result["as_of"]),
                        earnings_data,
                        5,
                    )
                    result["earnings_phase"] = phase
                except Exception:
                    result["earnings_phase"] = "clean"
            else:
                result["earnings_phase"] = "clean"
            candidates.append(result)
        except Exception:
            continue

    # 정렬: score → 거래량 → 모멘텀
    candidates.sort(
        key=lambda r: (
            r.get("total_score", 0),
            r.get("volume_score", 0),
            r.get("momentum_score", 0),
        ),
        reverse=True,
    )

    # score ≥ 4 (검증된 임계값) + earnings pre 차단
    filtered = [
        r for r in candidates
        if r.get("total_score", 0) >= 4 and r.get("earnings_phase") != "pre"
    ]

    out: list[PickCandidate] = []
    for i, r in enumerate(filtered[:top], start=1):
        out.append(
            PickCandidate(
                system_id="scanner",
                rank=i,
                symbol=r["symbol"],
                score=float(r.get("total_score", 0)),
                score_meta={
                    "signals": r.get("signals", {}),
                    "earnings_phase": r.get("earnings_phase"),
                    "vol_vs_20d_avg": r.get("vol_vs_20d_avg"),
                    "close": r.get("close"),
                    "historical": r.get("historical"),
                },
                sector=r.get("sector"),
                strategy_tag="swing",
            )
        )
    return out


# ─────────── Integrated (개발 중 — 추후 사용자가 채울 stub) ───────────


async def fetch_integrated_picks(
    session: AsyncSession, target_date: date, top: int = 5
) -> list[PickCandidate]:
    """통합 시스템 picks — v10 우선, 부족분은 v9 (auto-blacklist 없는 버전) fallback.

    v10이 auto-blacklist + drawdown gate로 종목을 0~2개만 통과시키는 날이 있어
    Top {top}을 매일 채우려면 v9로 보충. 메타에 source 표시.
    """
    try:
        from scanner.integrated.run import run_integrated_v10, run_integrated_v9

        v10_picks = await run_integrated_v10(target_date, top, session=session)
        for p in v10_picks:
            if isinstance(p.score_meta, dict):
                p.score_meta.setdefault("source", "v10")

        if len(v10_picks) >= top:
            return v10_picks

        # 부족분 v9에서 보충
        gap = top - len(v10_picks)
        v10_syms = {p.symbol for p in v10_picks}
        v9_picks = await run_integrated_v9(target_date, top + len(v10_picks), session=session)
        fillers: list[PickCandidate] = []
        next_rank = len(v10_picks) + 1
        for p in v9_picks:
            if p.symbol in v10_syms:
                continue
            if isinstance(p.score_meta, dict):
                p.score_meta["source"] = "v9_fallback"
            p.rank = next_rank
            next_rank += 1
            fillers.append(p)
            if len(fillers) >= gap:
                break

        if fillers:
            logger.info(
                "integrated picks v9 fallback: v10=%d + v9=%d for %s",
                len(v10_picks), len(fillers), target_date,
            )
        return v10_picks + fillers
    except Exception as exc:
        logger.warning("integrated picks failed: %s", exc)
        return []


# ─────────── Dashboard (Tier 기반 통합 추천) ───────────


async def fetch_dashboard_picks(target_date: date, top: int = 5) -> list[PickCandidate]:
    """대시보드 자체 평가 파이프라인의 top N picks.

    scanner와 동일한 시그널 베이스를 쓰지만 Tier 분류·ATR levels·historical을
    score_meta에 함께 실어 추적. score≥4 + earnings pre 차단 (scanner와 동일 필터).
    target_date가 미래/오늘이면 build_dashboard가 None(=today)으로 내부 처리.
    """
    try:
        from api.routes.dashboard import build_dashboard

        # build_dashboard는 target_date=None일 때 today 평가. 과거 일자는 그대로 전달.
        target_arg = target_date if target_date < date.today() else None
        resp = await build_dashboard(
            target_date=target_arg,
            score_min=4,
            earnings_mode="pre_only",
            top=top * 4,  # 후보 풀 넉넉히 잡고 아래에서 top N
        )
    except Exception as exc:
        logger.warning("dashboard picks fetch failed: %s", exc)
        return []

    # tiers dict → 단일 rank 정렬 리스트. (모든 tier 모아 rank 순)
    all_cands = []
    for tier_letter in ("S", "A", "B", "C"):
        all_cands.extend(resp.tiers.get(tier_letter, []))
    all_cands.sort(key=lambda c: c.rank)

    out: list[PickCandidate] = []
    for i, c in enumerate(all_cands[:top], start=1):
        levels = c.levels
        out.append(
            PickCandidate(
                system_id="dashboard",
                rank=i,
                symbol=c.symbol,
                score=float(c.total_score),
                score_meta={
                    "tier": c.tier,
                    "tier_path": c.tier_path,
                    "signals": c.signals,
                    "vol_vs_20d_avg": c.vol_vs_20d_avg,
                    "close": c.close,
                    "earnings_phase": c.earnings_phase,
                    "historical": c.historical,
                    "entry_price": levels.entry if levels else None,
                    "stop_price": levels.stop if levels else None,
                    "target_1r": levels.target_1r if levels else None,
                    "target_2r": levels.target_2r if levels else None,
                },
                sector=c.sector,
                strategy_tag="swing",
            )
        )
    return out


# ─────────── 통합 호출 ───────────


async def fetch_intraday_picks(
    session: AsyncSession, target_date: date, top: int = 5
) -> list[PickCandidate]:
    """단타 5-Model Stack picks (intraday_v1)."""
    try:
        from scanner.integrated.run import run_integrated_intraday

        picks = await run_integrated_intraday(target_date, top, session=session)
        # system_id 정규화 (logger가 system_id 컬럼으로 저장)
        for p in picks:
            p.system_id = "intraday"
            if isinstance(p.score_meta, dict):
                p.score_meta.setdefault("version", "intraday_v1")
        return picks
    except Exception as exc:
        logger.warning("intraday picks failed: %s", exc)
        return []


async def fetch_all_systems(
    session: AsyncSession, target_date: date, top: int = 5
) -> dict[str, list[PickCandidate]]:
    """5 시스템 모두 fetch (v3 / scanner / integrated / dashboard / intraday). 어댑터별 예외는 격리."""
    result: dict[str, list[PickCandidate]] = {}
    try:
        result["v3"] = await fetch_v3_picks(session, target_date, top)
    except Exception as exc:
        logger.warning("v3 picks fetch failed: %s", exc)
        result["v3"] = []
    try:
        result["scanner"] = await fetch_scanner_picks(target_date, top)
    except Exception as exc:
        logger.warning("scanner picks fetch failed: %s", exc)
        result["scanner"] = []
    try:
        result["integrated"] = await fetch_integrated_picks(session, target_date, top)
    except Exception as exc:
        logger.warning("integrated picks fetch failed: %s", exc)
        result["integrated"] = []
    try:
        result["dashboard"] = await fetch_dashboard_picks(target_date, top)
    except Exception as exc:
        logger.warning("dashboard picks fetch failed: %s", exc)
        result["dashboard"] = []
    try:
        result["intraday"] = await fetch_intraday_picks(session, target_date, top)
    except Exception as exc:
        logger.warning("intraday picks fetch failed: %s", exc)
        result["intraday"] = []
    return result
