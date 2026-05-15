"""Daily Trading Plan 엔드포인트 — Market Brief + Top 3 + 사용자 매매 plan 입력.

GET  /api/trading/today             — 오늘 Market Brief + Top 3 추천 + 기 저장 plan
GET  /api/trading/plans/{date}      — 특정 일자 저장된 plan + outcomes
GET  /api/trading/plans?days=30     — 최근 N일 plan 이력 + 누적 PnL
POST /api/trading/plan              — plan 저장 (한 종목)
DELETE /api/trading/plan/{id}       — plan 취소
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db import get_session
from api.db.models import TradePlan, TradePlanOutcome

router = APIRouter()


# ─────────── 응답 스키마 ───────────


class MarketBrief(BaseModel):
    regime_score: float
    regime_mode: str  # "aggressive" | "neutral" | "defensive"
    regime_signals: dict[str, bool]
    indices: dict[str, float]  # SPY/QQQ/IWM/SOXX/XLK/etc gap %
    summary: str
    position_size_multiplier: float
    long_blocked: bool


class ScoreBreakdownItem(BaseModel):
    name: str           # internal key (e.g. "v3_norm", "sector_bonus", "super_mult")
    label_ko: str       # 한글 라벨
    points: float       # 가산항목은 점수, multiplier는 (배수-1)*100 (=가산 효과 %)
    kind: str           # "base" | "bonus" | "multiplier"


class PickRecommendation(BaseModel):
    rank: int
    symbol: str
    sector: str | None
    composite_score: float
    tier: int | None
    entry_price: Decimal
    stop_price: Decimal
    target_1r: Decimal
    target_2r: Decimal
    risk_per_share: Decimal
    risk_pct: float  # (entry-stop)/entry × 100
    score_meta: dict[str, Any]
    consensus_systems: list[str] = Field(default_factory=list)
    consensus_tier: str = "B"  # "S" (3/3) | "A" (2/3) | "B" (1/3 — integrated만)
    score_breakdown: list[ScoreBreakdownItem] = Field(default_factory=list)
    # "v10" (스윙 기본) | "v9_fallback" (스윙 보충) | "intraday_v1" (단타)
    system_source: str = "v10"


def _resolve_system_source(score_meta: dict[str, Any] | None) -> str:
    """score_meta → system_source 결정. trade_plan / pick recommendation 공통 규약."""
    if not isinstance(score_meta, dict):
        return "v10"
    if score_meta.get("version") == "intraday_v1":
        return "intraday_v1"
    if score_meta.get("source") == "v9_fallback":
        return "v9_fallback"
    return "v10"


class TradePlanOut(BaseModel):
    id: int
    plan_date: date
    symbol: str
    rank: int
    amount_usd: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_1r: Decimal
    target_2r: Decimal
    composite_score: Decimal
    sector: str | None
    shares: int
    risk_usd: Decimal
    score_meta: dict[str, Any] = Field(default_factory=dict)
    # integrated 내부 sub-system: "v10" (기본) | "v9_fallback" | "intraday_v1".
    system_source: str = "v10"
    # Intraday 5-Model Stack 추가 필드 (Phase 5)
    confirm_status: str = "watchlist"
    # 'user_fixed' (사용자 직접 입력, 09:30 그대로 발송) | 'orb_auto' (스캐너 watchlist, 09:45 ORB 평가).
    dispatch_mode: str = "orb_auto"
    orb_high: Decimal | None = None
    orb_low: Decimal | None = None
    session_vwap: Decimal | None = None
    intraday_rvol: Decimal | None = None
    premarket_gap_pct: Decimal | None = None
    premarket_rvol: Decimal | None = None
    created_at: datetime
    outcomes: list["TradePlanOutcomeOut"] = []

    model_config = {"from_attributes": True}


def _trade_plan_to_out(plan: TradePlan) -> TradePlanOut:
    """TradePlan ORM → TradePlanOut. score_meta를 기반으로 system_source 결정."""
    system_source = _resolve_system_source(plan.score_meta)
    return TradePlanOut(
        id=plan.id,
        plan_date=plan.plan_date,
        symbol=plan.symbol,
        rank=plan.rank,
        amount_usd=plan.amount_usd,
        entry_price=plan.entry_price,
        stop_price=plan.stop_price,
        target_1r=plan.target_1r,
        target_2r=plan.target_2r,
        composite_score=plan.composite_score,
        sector=plan.sector,
        shares=plan.shares,
        risk_usd=plan.risk_usd,
        score_meta=plan.score_meta or {},
        system_source=system_source,
        confirm_status=plan.confirm_status,
        dispatch_mode=plan.dispatch_mode,
        orb_high=plan.orb_high,
        orb_low=plan.orb_low,
        session_vwap=plan.session_vwap,
        intraday_rvol=plan.intraday_rvol,
        premarket_gap_pct=plan.premarket_gap_pct,
        premarket_rvol=plan.premarket_rvol,
        created_at=plan.created_at,
        outcomes=[TradePlanOutcomeOut.model_validate(o) for o in plan.outcomes],
    )


class TradePlanOutcomeOut(BaseModel):
    horizon_days: int
    exit_date: date
    exit_price: Decimal
    pct_return: Decimal
    spy_pct_return: Decimal
    alpha: Decimal
    realized_pnl_usd: Decimal
    hit_target_1r: bool
    hit_target_2r: bool = False
    hit_stop: bool
    qty_sold_at_1r: int = 0
    qty_sold_at_2r: int = 0
    partial_realized_pnl_usd: Decimal = Decimal("0")

    model_config = {"from_attributes": True}


class TradingTodayResponse(BaseModel):
    plan_date: date
    market_brief: MarketBrief
    picks: list[PickRecommendation]
    existing_plans: list[TradePlanOut]


class TradePlanPayload(BaseModel):
    """amount_usd 또는 shares 중 하나는 필수.
    - amount_usd만: shares = floor(amount_usd / entry_price)
    - shares만: amount_usd = shares × entry_price
    - 둘 다: amount_usd 우선 (shares 재계산)
    """
    symbol: str
    rank: int = Field(default=1, ge=1, le=10)
    amount_usd: float | None = Field(default=None, gt=0, le=1_000_000)
    shares: int | None = Field(default=None, gt=0, le=1_000_000)
    entry_price: float
    stop_price: float
    target_1r: float
    target_2r: float
    composite_score: float = 0.0
    sector: str | None = None
    score_meta: dict[str, Any] = Field(default_factory=dict)


# ─────────── 핵심 헬퍼 ───────────


async def _build_market_brief() -> MarketBrief:
    """Regime + market context를 통합."""
    from scanner.regime import evaluate_regime
    from scanner.stage2_daily_picks import get_market_context

    # 동기 yfinance fetch는 thread로 → 이벤트 루프 안 막음
    regime = await asyncio.to_thread(evaluate_regime, date.today())
    market_ctx = await asyncio.to_thread(get_market_context, date.today())

    indices: dict[str, float] = {}
    for k, v in market_ctx.items():
        if k.endswith("_gap_pct") and isinstance(v, (int, float)):
            indices[k.replace("_gap_pct", "")] = round(float(v), 2)

    # 한 줄 요약
    if regime.long_blocked():
        summary = (
            f"⚠️ 방어모드 (regime {regime.score:.0f}/15) — long 진입 차단. "
            f"평균회귀 후보만 가능."
        )
    elif regime.mode == "neutral":
        summary = (
            f"중립 (regime {regime.score:.0f}/15) — 포지션 사이즈 ×0.7 권장. "
            f"선별적 진입."
        )
    else:
        summary = (
            f"🟢 공격모드 (regime {regime.score:.0f}/15) — 풀 사이즈 OK. "
            f"강세장 진입 우호."
        )

    # 섹터 강세 한 줄 추가
    sox = indices.get("SOXX")
    xlk = indices.get("XLK")
    if sox is not None and sox >= 1.5:
        summary += f" 반도체 강세 ({sox:+.1f}%)."
    elif xlk is not None and xlk >= 1.0:
        summary += f" 기술 섹터 강세 ({xlk:+.1f}%)."

    return MarketBrief(
        regime_score=round(regime.score, 1),
        regime_mode=regime.mode,
        regime_signals=regime.signals,
        indices=indices,
        summary=summary,
        position_size_multiplier=regime.position_size_multiplier(),
        long_blocked=regime.long_blocked(),
    )


# ─── score_breakdown 헬퍼 ───
# v10/v9 score_meta(scanner/integrated/run.py:520-542) 의 raw 값과 가중치 표를 적용해
# 각 component의 점수 기여를 역산. 사용자가 "왜 이 종목이 이 점수인지" 한눈에 보도록.
# v10 코드와 가중치 동기화 필수 — 변경 시 양쪽 함께.

_LABELS_KO: dict[str, str] = {
    "v3_norm":          "v3 기본 (Stage 1 setup)",
    "scanner_norm":     "스캐너 기본 (거래량+모멘텀)",
    "ce_norm":          "압축+팽창 패턴",
    "ol_norm":          "시가 위치 (gap)",
    "rsi_norm":         "RSI 구조",
    "sector_bonus":     "우선 섹터 (반도체/테크)",
    "sector_mom_bonus": "섹터 모멘텀 강세",
    "confluence_bonus": "스캐너 합의 (+4점 이상)",
    "stage2_bonus":     "Stage 2 trend template",
    "streak_bonus":     "v3 연속 추천 streak",
    "obv_bonus":        "OBV 누적 매수세",
    "mom_accel_bonus":  "모멘텀 가속 (1m>3m)",
    "avwap_bonus":      "Anchored VWAP 위",
    "feedback_bonus":   "최근 outcome 피드백",
    "earnings_mult":    "PEAD (post-earnings)",
    "super_mult":       "5중 합의 슈퍼 multiplier",
    "diversification_penalty": "섹터 분산 페널티",
}


def compute_score_breakdown(meta: dict[str, Any], top_n: int = 5) -> list[ScoreBreakdownItem]:
    """Integrated v10/v9 score_meta → 절대값 큰 순 top N 기여 항목 list.

    Tier 1 (v3_priority): v3_norm + ce/ol/rsi + bonuses × earnings_mult × super_mult
    Tier 2 (scanner_strict): scanner_norm + ce/ol/rsi + bonuses × ... (다른 가중치)
    """
    items: list[ScoreBreakdownItem] = []
    tier = int(meta.get("tier", 1) or 1)
    regime_mode = meta.get("regime_mode", "neutral")
    regime_boost = 1.2 if regime_mode == "aggressive" else 1.0
    golden = bool(meta.get("golden_setup"))
    stage2 = bool(meta.get("stage2_pass"))
    compression = bool(meta.get("compression"))
    rsi_grade = meta.get("rsi_grade")

    def add(name: str, points: float, kind: str):
        if abs(points) < 0.05:
            return
        items.append(ScoreBreakdownItem(
            name=name, label_ko=_LABELS_KO.get(name, name),
            points=round(points, 2), kind=kind,
        ))

    # Base components (tier별 가중치)
    if tier == 1:
        v3_score = float(meta.get("v3_score", 0) or 0)
        if v3_score > 0:
            add("v3_norm", ((v3_score / 100.0) ** 1.3) * 50, "base")
        ce_score = float(meta.get("compression_score", 0) or 0)
        ce_pts = (ce_score / 6.0) * 20 * regime_boost
        if golden: ce_pts += 10.0
        if stage2 and compression: ce_pts += 5.0
        add("ce_norm", ce_pts, "base")
        ol_score = float(meta.get("open_location_score", 0) or 0)
        add("ol_norm", (ol_score / 5.0) * 8, "base")
        rsi_pts = 5.0 if rsi_grade == "good" else 3.0 if rsi_grade == "ok" else 0.0
        add("rsi_norm", rsi_pts, "base")
    else:  # tier 2 (scanner_strict)
        scanner_score = float(meta.get("scanner_score", 0) or 0)
        add("scanner_norm", (scanner_score / 5.0) * 25, "base")
        ce_score = float(meta.get("compression_score", 0) or 0)
        ce_pts = (ce_score / 6.0) * 25 * regime_boost
        if golden: ce_pts += 8.0
        if stage2 and compression: ce_pts += 5.0
        add("ce_norm", ce_pts, "base")
        ol_score = float(meta.get("open_location_score", 0) or 0)
        add("ol_norm", (ol_score / 5.0) * 12, "base")
        rsi_pts = 8.0 if rsi_grade == "good" else 5.0 if rsi_grade == "ok" else 0.0
        add("rsi_norm", rsi_pts, "base")

    # Bonuses (둘 다 동일 — meta에 raw로 저장됨)
    for key in (
        "sector_bonus", "sector_mom_bonus", "confluence_bonus",
        "stage2_bonus", "streak_bonus", "obv_bonus",
        "mom_accel_bonus", "avwap_bonus", "feedback_bonus",
    ):
        add(key, float(meta.get(key, 0) or 0), "bonus")

    # Multipliers — (배수-1)*100 = "% 가산 효과"로 표시
    em = float(meta.get("earnings_multiplier", 1.0) or 1.0)
    if abs(em - 1.0) > 0.001:
        add("earnings_mult", (em - 1.0) * 100, "multiplier")
    sm = float(meta.get("super_multiplier", 1.0) or 1.0)
    if abs(sm - 1.0) > 0.001:
        add("super_mult", (sm - 1.0) * 100, "multiplier")

    # Penalty (음수)
    div = float(meta.get("diversification_penalty", 0) or 0)
    if div > 0:
        add("diversification_penalty", -div, "bonus")

    items.sort(key=lambda x: abs(x.points), reverse=True)
    return items[:top_n]


def _consensus_tier(consensus_systems: list[str]) -> str:
    """integrated 외 매칭 카운트 기준 — v3+scanner 둘 다=S, 하나=A, 없음=B."""
    others = [s for s in consensus_systems if s != "integrated"]
    if len(others) >= 2: return "S"
    if len(others) == 1: return "A"
    return "B"


async def _build_picks(session: AsyncSession, top: int = 3) -> list[PickRecommendation]:
    """스윙(통합 v10/v9 fallback)과 단타(intraday_v1) picks를 결합해 응답.

    swing entry/stop은 stage2_daily_picks의 compute_pick_metadata로 산출.
    intraday는 score_meta.provisional_* 값을 그대로 entry/stop/1R/2R로 사용.
    """
    from scanner.comparison.adapters import (
        fetch_integrated_picks,
        fetch_scanner_picks,
        fetch_v3_picks,
    )
    from scanner.integrated.run import run_integrated_intraday
    from scanner.stage2_daily_picks import (
        DEFAULT_ACCOUNT_EQUITY,
        compute_pick_metadata,
        fetch_candidate_metrics,
    )
    import yfinance as yf

    today = date.today()
    integrated_picks = await fetch_integrated_picks(session, today, top=top)
    # 단타 — regime 차단/게이트 미충족 시 빈 리스트
    try:
        intraday_picks = await run_integrated_intraday(today, top=top, session=session)
    except Exception:
        intraday_picks = []

    # 합의 등급 계산용 — v3/scanner의 top 10을 set으로
    v3_syms: set[str] = set()
    scanner_syms: set[str] = set()
    try:
        v3_other = await fetch_v3_picks(session, today, top=10)
        v3_syms = {p.symbol for p in v3_other}
    except Exception:
        pass
    try:
        sc_other = await fetch_scanner_picks(today, top=10)
        scanner_syms = {p.symbol for p in sc_other}
    except Exception:
        pass

    out: list[PickRecommendation] = []

    for p in integrated_picks:
        # 종목 메트릭 fetch (entry/stop/1R/2R 계산용)
        # yfinance 동기 호출은 모두 asyncio.to_thread로 → 이벤트 루프 안 막음
        try:
            m = await asyncio.to_thread(fetch_candidate_metrics, p.symbol, date.today())
            from backtests.data_cache import get_bars

            end_iso = date.today().isoformat()
            start_iso = (date.today() - timedelta(days=60)).isoformat()
            try:
                daily_bars = await asyncio.to_thread(get_bars, p.symbol, start_iso, end_iso, "1d")
            except Exception:
                daily_bars = None

            intraday_bars = None
            try:
                idf = await asyncio.to_thread(
                    yf.download, p.symbol,
                    period="5d", interval="5m",
                    progress=False, auto_adjust=False,
                )
                if idf is not None and not idf.empty:
                    import pandas as pd
                    if isinstance(idf.columns, pd.MultiIndex):
                        idf.columns = idf.columns.get_level_values(0)
                    idf.columns = [c.lower() for c in idf.columns]
                    intraday_bars = idf
            except Exception:
                pass

            pivot, stop, t1, t2, risk_per_share, _size, _tag = await asyncio.to_thread(
                compute_pick_metadata,
                m, daily_bars, intraday_bars, DEFAULT_ACCOUNT_EQUITY,
            )
        except Exception:
            # Fallback: score_meta에 있으면 사용, 없으면 prev_close 기반 추정
            sm = p.score_meta or {}
            pivot = float(sm.get("pivot_price") or sm.get("entry_price") or 0)
            stop = float(sm.get("stop_price") or pivot * 0.95)
            t1 = pivot + (pivot - stop)
            t2 = pivot + 2 * (pivot - stop)
            risk_per_share = pivot - stop

        if pivot <= 0:
            continue

        risk_pct = ((pivot - stop) / pivot * 100.0) if pivot > 0 else 0.0

        meta = p.score_meta or {}
        consensus = ["integrated"]
        if p.symbol in v3_syms:
            consensus.append("v3")
        if p.symbol in scanner_syms:
            consensus.append("scanner")
        tier_letter = _consensus_tier(consensus)
        breakdown = compute_score_breakdown(meta)

        out.append(
            PickRecommendation(
                rank=p.rank,
                symbol=p.symbol,
                sector=p.sector,
                composite_score=float(p.score),
                tier=int(meta.get("tier", 0)) or None,
                entry_price=Decimal(f"{pivot:.4f}"),
                stop_price=Decimal(f"{stop:.4f}"),
                target_1r=Decimal(f"{t1:.4f}"),
                target_2r=Decimal(f"{t2:.4f}"),
                risk_per_share=Decimal(f"{risk_per_share:.4f}"),
                risk_pct=round(risk_pct, 2),
                score_meta=meta,
                consensus_systems=consensus,
                consensus_tier=tier_letter,
                score_breakdown=breakdown,
                system_source=_resolve_system_source(meta),
            )
        )

    # 단타 picks 합류 — entry/stop/1R/2R는 provisional_* meta 그대로 사용
    for p in intraday_picks:
        meta = p.score_meta or {}
        entry = float(meta.get("provisional_entry") or meta.get("premarket_close") or 0)
        stop = float(meta.get("provisional_stop") or 0)
        t1 = float(meta.get("provisional_target_1r") or 0)
        t2 = float(meta.get("provisional_target_2r") or 0)
        if entry <= 0 or stop <= 0 or stop >= entry:
            continue
        risk_per_share = entry - stop
        risk_pct = (risk_per_share / entry) * 100.0
        out.append(
            PickRecommendation(
                rank=p.rank,
                symbol=p.symbol,
                sector=p.sector,
                composite_score=float(p.score),
                tier=None,  # 단타는 swing tier 개념 무관
                entry_price=Decimal(f"{entry:.4f}"),
                stop_price=Decimal(f"{stop:.4f}"),
                target_1r=Decimal(f"{t1:.4f}"),
                target_2r=Decimal(f"{t2:.4f}"),
                risk_per_share=Decimal(f"{risk_per_share:.4f}"),
                risk_pct=round(risk_pct, 2),
                score_meta=meta,
                consensus_systems=["intraday"],
                consensus_tier="B",
                score_breakdown=[],  # swing v10 weight 표 기반이라 단타엔 무의미
                system_source="intraday_v1",
            )
        )
    return out


# ─────────── 엔드포인트 ───────────


# brief + picks 캐시. yfinance 라이브 fetch가 느려서(60s+) 매 호출마다 재계산하면
# 폰에서 못 씀. existing_plans는 사용자가 즉시 추가/삭제하므로 캐싱 X — 매번 DB 조회.
_TRADING_CACHE: dict[str, Any] = {"key": None, "brief": None, "picks": None, "ts": 0.0}
_TRADING_CACHE_LOCK = asyncio.Lock()
_TRADING_CACHE_TTL_SEC = 3600  # 1시간 (백그라운드 워밍이 30분마다 갱신하므로 사실상 항상 유효)
_TRADING_CACHE_WARM_INTERVAL_SEC = 1800  # 30분


async def warm_trading_cache_loop() -> None:
    """백그라운드 task: 30분마다 trading 캐시 갱신.

    yfinance 라이브 fetch가 느려서(60-90s) 사용자 첫 요청이 timeout 위험.
    이 loop가 미리 채워두면 항상 즉답 가능.
    캐시 TTL(1h)보다 짧은 주기로 갱신 → 항상 fresh.
    """
    import logging

    from api.db import async_session_factory

    logger = logging.getLogger("trading.cache")
    while True:
        try:
            async with async_session_factory() as session:
                _TRADING_CACHE["ts"] = 0.0  # 강제 재계산
                await _get_cached_brief_and_picks(session)
            logger.info("trading cache warmed")
        except Exception as exc:
            logger.warning(f"trading cache warm failed: {exc}")
        await asyncio.sleep(_TRADING_CACHE_WARM_INTERVAL_SEC)


async def _get_cached_brief_and_picks(
    session: AsyncSession,
) -> tuple[MarketBrief, list[PickRecommendation]]:
    today_key = date.today().isoformat()
    now = time.monotonic()
    if (
        _TRADING_CACHE["key"] == today_key
        and now - _TRADING_CACHE["ts"] < _TRADING_CACHE_TTL_SEC
    ):
        return _TRADING_CACHE["brief"], _TRADING_CACHE["picks"]
    async with _TRADING_CACHE_LOCK:
        # double-check after acquiring lock
        now = time.monotonic()
        if (
            _TRADING_CACHE["key"] == today_key
            and now - _TRADING_CACHE["ts"] < _TRADING_CACHE_TTL_SEC
        ):
            return _TRADING_CACHE["brief"], _TRADING_CACHE["picks"]
        brief = await _build_market_brief()
        # 5-Model Intraday Stack: top 5 watchlist (was top 3)
        picks = await _build_picks(session, top=5)
        _TRADING_CACHE.update({"key": today_key, "brief": brief, "picks": picks, "ts": time.monotonic()})
        return brief, picks


@router.get("/today", response_model=TradingTodayResponse)
async def get_today(
    refresh: bool = Query(default=False, description="True면 캐시 무시하고 재계산"),
    session: AsyncSession = Depends(get_session),
) -> TradingTodayResponse:
    today = date.today()

    if refresh:
        _TRADING_CACHE["ts"] = 0.0  # invalidate

    brief, picks = await _get_cached_brief_and_picks(session)

    # 이미 저장된 오늘 plan (캐시 X)
    stmt = (
        select(TradePlan)
        .options(selectinload(TradePlan.outcomes))
        .where(TradePlan.plan_date == today)
        .order_by(TradePlan.rank)
    )
    existing = list((await session.execute(stmt)).scalars().all())

    return TradingTodayResponse(
        plan_date=today,
        market_brief=brief,
        picks=picks,
        existing_plans=[_trade_plan_to_out(p) for p in existing],
    )


@router.get("/plans/{plan_date}", response_model=list[TradePlanOut])
async def get_plans_for_date(
    plan_date: date,
    session: AsyncSession = Depends(get_session),
) -> list[TradePlanOut]:
    stmt = (
        select(TradePlan)
        .options(selectinload(TradePlan.outcomes))
        .where(TradePlan.plan_date == plan_date)
        .order_by(TradePlan.rank)
    )
    return [_trade_plan_to_out(p) for p in (await session.execute(stmt)).scalars().all()]


@router.get("/plans", response_model=list[TradePlanOut])
async def list_plans(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> list[TradePlanOut]:
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(TradePlan)
        .options(selectinload(TradePlan.outcomes))
        .where(TradePlan.plan_date >= cutoff)
        .order_by(TradePlan.plan_date.desc(), TradePlan.rank)
    )
    return [_trade_plan_to_out(p) for p in (await session.execute(stmt)).scalars().all()]


@router.post("/plan", response_model=TradePlanOut)
async def save_plan(
    payload: TradePlanPayload,
    session: AsyncSession = Depends(get_session),
) -> TradePlanOut:
    """동일 (plan_date, symbol) upsert. amount_usd 또는 shares 입력 시 다른 쪽 자동 산출."""
    if payload.entry_price <= 0:
        raise HTTPException(status_code=400, detail="entry_price must be positive")
    if payload.amount_usd is None and payload.shares is None:
        raise HTTPException(
            status_code=400,
            detail="amount_usd 또는 shares 중 최소 하나는 입력해야 합니다",
        )

    today = date.today()
    if payload.amount_usd is not None:
        shares = math.floor(payload.amount_usd / payload.entry_price)
        amount_usd_eff = float(payload.amount_usd)
        if shares <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"amount_usd ${payload.amount_usd} too small for entry ${payload.entry_price} (shares=0)",
            )
    else:
        shares = int(payload.shares or 0)
        amount_usd_eff = shares * payload.entry_price
    risk_per_share = payload.entry_price - payload.stop_price
    risk_usd = shares * risk_per_share if risk_per_share > 0 else 0.0

    row = {
        "plan_date": today,
        "symbol": payload.symbol.upper(),
        "rank": payload.rank,
        "amount_usd": Decimal(f"{amount_usd_eff:.2f}"),
        "entry_price": Decimal(f"{payload.entry_price:.4f}"),
        "stop_price": Decimal(f"{payload.stop_price:.4f}"),
        "target_1r": Decimal(f"{payload.target_1r:.4f}"),
        "target_2r": Decimal(f"{payload.target_2r:.4f}"),
        "composite_score": Decimal(f"{payload.composite_score:.2f}"),
        "score_meta": payload.score_meta,
        "sector": payload.sector,
        "shares": shares,
        "risk_usd": Decimal(f"{risk_usd:.2f}"),
        # 사용자 직접 입력 → user_fixed. run_trade(09:30)가 입력값 그대로 발송.
        "dispatch_mode": "user_fixed",
    }

    stmt = pg_insert(TradePlan).values(row)
    update_cols = {
        "rank": stmt.excluded.rank,
        "amount_usd": stmt.excluded.amount_usd,
        "entry_price": stmt.excluded.entry_price,
        "stop_price": stmt.excluded.stop_price,
        "target_1r": stmt.excluded.target_1r,
        "target_2r": stmt.excluded.target_2r,
        "composite_score": stmt.excluded.composite_score,
        "score_meta": stmt.excluded.score_meta,
        "sector": stmt.excluded.sector,
        "shares": stmt.excluded.shares,
        "risk_usd": stmt.excluded.risk_usd,
        # dispatch_mode='user_fixed'로 항상 덮어씀 — 스캐너 orb_auto가 먼저 들어있어도
        # 사용자가 명시적으로 가격 입력하면 user_fixed 우선.
        "dispatch_mode": stmt.excluded.dispatch_mode,
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_trade_plan_date_sym", set_=update_cols
    )
    await session.execute(stmt)
    await session.commit()

    # 저장된 row 다시 fetch (outcomes 포함)
    fetch_stmt = (
        select(TradePlan)
        .options(selectinload(TradePlan.outcomes))
        .where(
            TradePlan.plan_date == today,
            TradePlan.symbol == payload.symbol.upper(),
        )
    )
    saved = (await session.execute(fetch_stmt)).scalar_one()
    return _trade_plan_to_out(saved)


@router.delete("/plan/{plan_id}")
async def delete_plan(
    plan_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(TradePlan).where(TradePlan.id == plan_id)
    plan = (await session.execute(stmt)).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    await session.delete(plan)
    await session.commit()
    return {"status": "deleted", "id": plan_id}
