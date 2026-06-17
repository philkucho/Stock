"""중장기 Fidelity 추천 endpoints — alembic 0014, 2026-06-05.

GET  /api/longterm/current        — 최신 pick_month 의 picks
GET  /api/longterm/history/{m}    — 특정 pick_month
GET  /api/longterm/months         — 사용 가능한 pick_month 리스트
GET  /api/longterm/outcomes       — alpha tracking (21/63/126/252d)
POST /api/longterm/refresh        — 수동 재선정 (dry-run 옵션)
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import LongtermOutcome, LongtermPick

router = APIRouter()


class LongtermPickOut(BaseModel):
    id: int
    pick_month: date
    rank: int
    symbol: str
    sector: str | None
    composite_score: Decimal
    gate_results: dict[str, Any]
    score_breakdown: dict[str, Any]
    weight_pct: Decimal
    status: str
    fidelity_action: str
    prev_pick_id: int | None
    created_at: datetime
    # 라이브 현재가 (yfinance fast_info, /current 한정 — 실패 시 None)
    current_price: float | None = None
    day_change_pct: float | None = None

    model_config = {"from_attributes": True}


def _fetch_quote(symbol: str) -> dict | None:
    """현재가 + 전일대비 % — yfinance fast_info (advisor context_builder 패턴)."""
    try:
        import yfinance as yf

        info = yf.Ticker(symbol).fast_info
        last = float(info.get("lastPrice") or info.get("regularMarketPrice") or 0)
        prev = float(info.get("previousClose") or 0)
        if last <= 0:
            return None
        return {
            "current_price": round(last, 2),
            "day_change_pct": round((last / prev - 1) * 100, 2) if prev > 0 else None,
        }
    except Exception:
        return None


class LongtermOutcomeOut(BaseModel):
    id: int
    pick_id: int
    symbol: str
    pick_month: date
    eval_date: date
    days_held: int
    pct_return: Decimal
    spy_pct_return: Decimal
    alpha: Decimal

    model_config = {"from_attributes": True}


class CurrentSummary(BaseModel):
    pick_month: date | None
    regime: str  # "ok" | "defensive" | "unknown"
    new_count: int
    hold_count: int
    exit_suggested_count: int
    exited_count: int
    last_refreshed_at: datetime | None
    picks: list[LongtermPickOut]


@router.get("/months", response_model=list[date])
async def list_months(session: AsyncSession = Depends(get_session)) -> list[date]:
    stmt = select(LongtermPick.pick_month).distinct().order_by(desc(LongtermPick.pick_month))
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/current", response_model=CurrentSummary)
async def get_current(session: AsyncSession = Depends(get_session)) -> CurrentSummary:
    # 최신 pick_month
    month_stmt = (
        select(LongtermPick.pick_month)
        .order_by(desc(LongtermPick.pick_month))
        .limit(1)
    )
    latest_month = (await session.execute(month_stmt)).scalar_one_or_none()

    if latest_month is None:
        return CurrentSummary(
            pick_month=None, regime="unknown",
            new_count=0, hold_count=0, exit_suggested_count=0, exited_count=0,
            last_refreshed_at=None, picks=[],
        )

    stmt = (
        select(LongtermPick)
        .where(LongtermPick.pick_month == latest_month)
        .order_by(LongtermPick.rank, LongtermPick.symbol)
    )
    picks = list((await session.execute(stmt)).scalars().all())

    new_c = sum(1 for p in picks if p.status == "new")
    hold_c = sum(1 for p in picks if p.status == "hold")
    es_c = sum(1 for p in picks if p.status == "exit_suggested")
    ex_c = sum(1 for p in picks if p.status == "exited")

    # regime: gate_results 확인 — 전부 빈 dict면 defensive 추정
    has_active = any(p.gate_results for p in picks if p.status in ("new", "hold"))
    regime = "ok" if has_active else "defensive"

    last_refreshed = max((p.created_at for p in picks), default=None)

    # 활성 종목 현재가 병렬 조회 (실패해도 picks는 반환 — quote만 None)
    active_syms = [p.symbol for p in picks if p.status != "exited"]
    quotes = await asyncio.gather(
        *[asyncio.to_thread(_fetch_quote, s) for s in active_syms]
    )
    quote_map = dict(zip(active_syms, quotes))

    return CurrentSummary(
        pick_month=latest_month,
        regime=regime,
        new_count=new_c, hold_count=hold_c,
        exit_suggested_count=es_c, exited_count=ex_c,
        last_refreshed_at=last_refreshed,
        picks=[
            LongtermPickOut.model_validate(p).model_copy(
                update=quote_map.get(p.symbol) or {}
            )
            for p in picks
        ],
    )


# ───────────────────────── 진입 시점(WHEN) 판단 ─────────────────────────
# 백테스트 근거 [[project_contrarian_fear_entry]]: 중장기 수익은 "어떤 종목(WHAT)"
# 만큼 "언제 진입(WHEN)"이 좌우. 현 추천은 near_52w_high 게이트라 항상 고점 근처
# 에서만 종목을 내놓음 → 공포 구간 진입 타이밍을 별도 신호로 보여준다.

class TimingOut(BaseModel):
    tier: str               # extreme_fear | fear | pullback | neutral | greed
    label: str              # 한글 배지
    verdict: str            # optimal | good | ok | neutral | suboptimal
    headline: str           # 한 줄 결론
    vix: float | None
    spy_drawdown_pct: float | None   # 52주 고점 대비 (음수)
    regime_mode: str        # aggressive | neutral | defensive | unknown
    reasons: list[str]      # 왜 그런지 (수치 근거)
    action: str             # 진입한다면 어떻게
    sizing: str             # 분할 제안


def _compute_timing() -> dict:
    """현재 시장의 진입 시점 등급 산출 (VIX + SPY 드로다운 + 레짐)."""
    from scanner.benchmarks import get_benchmark_bars
    from scanner.regime import evaluate_regime

    # SPY 52주 고점 대비 드로다운
    dd: float | None = None
    spy = get_benchmark_bars("SPY", lookback_days=400)
    if spy is not None and len(spy) >= 60:
        high_252 = float(spy["high"].tail(252).max())
        last = float(spy["close"].iloc[-1])
        if high_252 > 0:
            dd = last / high_252 - 1.0

    # VIX — 직접 fetch, 실패 시 로컬 캐시 폴백 (공포 게이지 핵심 입력)
    vix: float | None = None
    vb = get_benchmark_bars("^VIX", lookback_days=30)
    if vb is not None and not vb.empty:
        vix = float(vb["close"].iloc[-1])
    else:
        try:
            from backtests.data_cache import get_bars as _cache_bars
            vb2 = _cache_bars("^VIX", "2024-01-01", date.today().isoformat())
            if vb2 is not None and not vb2.empty:
                vix = float(vb2["close"].iloc[-1])
        except Exception:
            pass

    # 레짐 모드
    mode = "unknown"
    try:
        mode = evaluate_regime().mode
    except Exception:
        pass

    mode_kr = {"aggressive": "공격(상승)", "neutral": "중립", "defensive": "방어(하락)"}.get(mode, "확인 불가")
    reasons: list[str] = []
    if dd is not None:
        reasons.append(f"S&P 500(SPY)이 52주 고점 대비 {dd*100:+.1f}%")
    if vix is not None:
        reasons.append(f"공포지수(VIX) {vix:.0f}" + (" — 극심한 공포" if vix > 30 else " — 안정" if vix < 16 else " — 다소 불안"))
    reasons.append(f"시장 국면: {mode_kr}")

    d = dd if dd is not None else 0.0
    v = vix if vix is not None else 18.0

    # 분류 (백테스트: VIX>30 & DD≤-15% = 역사적 최적 진입 구간)
    if v > 30 and d <= -0.15:
        tier, verdict = "extreme_fear", "optimal"
        label = "🔥 극공포 — 최적 진입 구간"
        headline = "역사적으로 가장 좋은 진입 시점입니다. 공포에 분할 매수하세요."
        action = "지금이 적기입니다. 자본을 3분할로 나눠 아래 종목에 분할 진입하세요(한 번에 전량 금지)."
        sizing = "1차 1/3 즉시 · 추가 하락마다 1/3씩 (총 3분할)"
    elif (v > 25 and d <= -0.10) or d <= -0.15:
        tier, verdict = "fear", "good"
        label = "😨 공포 — 우호적 진입 구간"
        headline = "진입에 유리한 구간입니다. 분할로 들어가세요."
        action = "우호적입니다. 아래 종목에 분할 진입하되, 더 깊은 공포(VIX 30+/-15%)가 오면 추가 매수 여력을 남겨두세요."
        sizing = "1차 1/3~1/2 · 추가 하락 시 잔여 분할"
    elif d <= -0.07:
        tier, verdict = "pullback", "ok"
        label = "😐 조정 — 소량 분할 또는 대기"
        headline = "얕은 조정입니다. 시점상 나쁘진 않지만 최적도 아닙니다."
        action = "진입한다면 아래 종목을 소량 분할로. 본진은 더 깊은 공포 구간까지 현금 일부를 남겨두는 편이 유리합니다."
        sizing = "소량(≤1/3) · 나머지 현금 대기"
    elif v < 16 and d > -0.03:
        tier, verdict = "greed", "suboptimal"
        label = "🤑 고점·낙관 — 시점 비최적"
        headline = "시점상 최적이 아닙니다(고점 근처·낮은 공포). 신규 자본은 서두르지 마세요."
        action = "진입한다면 아래 종목이 후보지만, 신규 자본은 소량만 넣고 조정·공포 구간까지 분할 대기를 권장합니다."
        sizing = "소량(≤1/3) 또는 공포 구간까지 대기"
    else:
        tier, verdict = "neutral", "neutral"
        label = "😶 중립 — 평범한 시점"
        headline = "시점은 평범합니다. 서두를 이유도, 피할 이유도 크지 않습니다."
        action = "진입한다면 아래 종목을 소량·분할로. 본진 투입은 공포 구간을 기다리는 편이 데이터상 유리합니다."
        sizing = "소량~중간 분할 · 본진은 대기"

    # 추세추종 시스템의 구조적 모순 안내: 공포인데 방어모드면 추천 리스트가 비어있음
    if verdict in ("optimal", "good") and mode == "defensive":
        action += (
            " ※ 참고: 현 추천 시스템은 추세추종이라 이 공포 구간에선 신규 종목을 내놓지 않습니다"
            "(방어 모드). 직전 정상장의 추천 종목(아래 보유 목록)을 분할 진입 후보로 활용하세요."
        )

    return {
        "tier": tier, "label": label, "verdict": verdict, "headline": headline,
        "vix": round(vix, 1) if vix is not None else None,
        "spy_drawdown_pct": round(dd * 100, 1) if dd is not None else None,
        "regime_mode": mode, "reasons": reasons, "action": action, "sizing": sizing,
    }


@router.get("/timing", response_model=TimingOut)
async def get_timing() -> TimingOut:
    """현재 진입 시점 등급 — 공포/탐욕 기반 WHEN 신호 (블로킹 호출은 thread)."""
    result = await asyncio.to_thread(_compute_timing)
    return TimingOut(**result)


@router.get("/history/{pick_month}", response_model=list[LongtermPickOut])
async def get_history(
    pick_month: date, session: AsyncSession = Depends(get_session)
) -> list[LongtermPickOut]:
    stmt = (
        select(LongtermPick)
        .where(LongtermPick.pick_month == pick_month)
        .order_by(LongtermPick.rank, LongtermPick.symbol)
    )
    picks = list((await session.execute(stmt)).scalars().all())
    if not picks:
        raise HTTPException(404, f"No picks for {pick_month}")
    return [LongtermPickOut.model_validate(p) for p in picks]


@router.get("/outcomes")
async def get_outcomes(
    horizon: int = Query(21, description="21 / 63 / 126 / 252"),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """horizon별 alpha 분포 + 평균."""
    stmt = (
        select(LongtermOutcome, LongtermPick)
        .join(LongtermPick, LongtermOutcome.pick_id == LongtermPick.id)
        .where(LongtermOutcome.days_held == horizon)
        .order_by(desc(LongtermOutcome.eval_date))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    out_list = []
    for oc, pick in rows:
        out_list.append({
            "pick_month": pick.pick_month.isoformat(),
            "symbol": pick.symbol,
            "eval_date": oc.eval_date.isoformat(),
            "days_held": oc.days_held,
            "pct_return": float(oc.pct_return),
            "spy_pct_return": float(oc.spy_pct_return),
            "alpha": float(oc.alpha),
            "status_at_eval": oc.status_at_eval,
        })
    # aggregates
    if rows:
        alphas = [float(oc.alpha) for oc, _ in rows]
        wins = sum(1 for a in alphas if a > 0)
        avg_alpha = sum(alphas) / len(alphas)
    else:
        wins, avg_alpha = 0, 0.0
    return {
        "horizon_days": horizon,
        "count": len(rows),
        "win_alpha": wins,
        "win_rate_pct": round(wins / len(rows) * 100, 1) if rows else 0,
        "avg_alpha_pct": round(avg_alpha, 3),
        "outcomes": out_list,
    }


def _weekly_volume_series(symbol: str, weeks: int = 52) -> list[dict]:
    """일봉(get_bars 캐시) → 주별 거래량/거래대금 시리즈. 최신순."""
    from datetime import date as _date, timedelta as _td

    from backtests.data_cache import get_bars

    end = _date.today()
    start = end - _td(days=weeks * 7 + 14)
    try:
        bars = get_bars(symbol, start.isoformat(), end.isoformat())
        if bars is None or bars.empty:
            return []
        weekly = bars.resample("W").agg({
            "volume": "sum",
            "close": "last",
            "high": "max",
            "low": "min",
        }).dropna()
        weekly["dollar_volume"] = weekly["volume"] * weekly["close"]
        out = []
        for ts, row in weekly.tail(weeks).iterrows():
            out.append({
                "date": ts.date().isoformat(),
                "volume": int(row["volume"]),
                "dollar_volume_musd": round(float(row["dollar_volume"]) / 1_000_000, 1),
                "close": round(float(row["close"]), 2),
            })
        return list(reversed(out))  # 최신순
    except Exception:
        return []


@router.get("/detail/{symbol}")
async def get_detail(symbol: str) -> dict:
    """종목 상세 — 분기 매출/마진/현금흐름/부채 + 밸류에이션 + 주별 거래량 + 체크리스트.

    캐시 우선 (30일 TTL). Miss 시 yfinance fetch (1종목당 1-3초).
    """
    from datetime import date as _date

    from scanner.longterm.fundamentals import fetch_one_detail

    r = await asyncio.to_thread(fetch_one_detail, symbol.upper())
    if r is None:
        raise HTTPException(404, f"Fundamentals not available for {symbol}")

    # 주별 거래량 (get_bars 캐시 활용, 빠름)
    weekly_volume = await asyncio.to_thread(_weekly_volume_series, symbol.upper(), 52)

    # ── 파생 계산 ──
    def _latest(series):
        return series[0]["value"] if series and series[0].get("value") is not None else None

    def _safe_div(a, b):
        return (a / b) if (a is not None and b is not None and b != 0) else None

    debt = _latest(r.get("quarterly_total_debt"))
    equity = _latest(r.get("quarterly_equity"))
    ca = _latest(r.get("quarterly_current_assets"))
    cl = _latest(r.get("quarterly_current_liab"))
    ocf = _latest(r.get("quarterly_ocf"))
    ni = _latest(r.get("quarterly_net_income"))
    fcf = _latest(r.get("quarterly_fcf"))
    rev = _latest(r.get("quarterly_revenue"))
    gp = _latest(r.get("quarterly_gross_profit"))
    op = _latest(r.get("quarterly_operating_income"))

    de_ratio = _safe_div(debt, equity)
    current_ratio = _safe_div(ca, cl)
    ocf_ni_ratio = _safe_div(ocf, ni)
    gross_margin = _safe_div(gp, rev)
    op_margin = _safe_div(op, rev)
    net_margin = _safe_div(ni, rev)
    fcf_margin = _safe_div(fcf, rev)

    # 52w high distance
    cur = r.get("current_price")
    high_52 = r.get("fifty_two_week_high")
    low_52 = r.get("fifty_two_week_low")
    high_dist = (cur / high_52 - 1) if (cur and high_52) else None

    # Days to next earnings
    days_to_er = None
    if r.get("next_earnings_date"):
        try:
            er = _date.fromisoformat(r["next_earnings_date"])
            days_to_er = (er - _date.today()).days
        except Exception:
            pass

    # 매출 QoQ 가속도 (최근 분기 성장 - 직전 분기 성장)
    qrev = r.get("quarterly_revenue") or []
    qoq_accel_rev = None
    if len(qrev) >= 3:
        try:
            g1 = (qrev[0]["value"] / qrev[1]["value"]) - 1
            g2 = (qrev[1]["value"] / qrev[2]["value"]) - 1
            qoq_accel_rev = g1 - g2
        except Exception:
            pass

    # ── 체크리스트 (8개, green/yellow/red) ──
    def _grade(value, green_threshold, yellow_threshold, better="higher"):
        if value is None:
            return "unknown"
        if better == "higher":
            if value >= green_threshold:
                return "green"
            if value >= yellow_threshold:
                return "yellow"
            return "red"
        else:  # lower is better
            if value <= green_threshold:
                return "green"
            if value <= yellow_threshold:
                return "yellow"
            return "red"

    checklist = [
        {
            "key": "debt", "label": "부채비율 (D/E)",
            "value": round(de_ratio, 2) if de_ratio is not None else None,
            "status": _grade(de_ratio, 1.0, 2.0, "lower"),
            "comment": "1.0 미만 안전 · 2.0 이상 부담",
        },
        {
            "key": "liquidity", "label": "유동비율",
            "value": round(current_ratio, 2) if current_ratio is not None else None,
            "status": _grade(current_ratio, 1.5, 1.0, "higher"),
            "comment": "1.5 이상 안전 · 1.0 미만 단기 위기",
        },
        {
            "key": "earnings_quality", "label": "이익의 질 (OCF / 순이익)",
            "value": round(ocf_ni_ratio, 2) if ocf_ni_ratio is not None else None,
            "status": _grade(ocf_ni_ratio, 0.9, 0.7, "higher"),
            "comment": "0.9 이상 안전 · 0.7 미만 회계 의심",
        },
        {
            "key": "earnings_proximity", "label": "다음 어닝까지",
            "value": days_to_er,
            "status": "green" if (days_to_er is None or days_to_er > 14)
                else "yellow" if days_to_er > 7
                else "red",
            "comment": "14일 초과 안전 · 7일 이하 진입 보류 권고"
                + (f" ({r.get('next_earnings_date')})" if r.get("next_earnings_date") else ""),
        },
        {
            "key": "52w_high_dist", "label": "52주 고가 거리",
            "value": round(high_dist * 100, 1) if high_dist is not None else None,
            "status": "red" if high_dist is not None and high_dist > -0.05
                else "yellow" if high_dist is not None and high_dist > -0.10
                else "green",
            "comment": "-5% 이내 정점 위험 · -10% 미만 진입 양호",
        },
        {
            "key": "beta", "label": "베타 (시장 민감도)",
            "value": round(r["beta"], 2) if r.get("beta") else None,
            "status": _grade(r.get("beta"), 1.3, 1.8, "lower"),
            "comment": "1.3 미만 안정 · 1.8 이상 충격 시 변동성 큼",
        },
        {
            "key": "rev_qoq_accel", "label": "매출 QoQ 가속도",
            "value": round(qoq_accel_rev * 100, 1) if qoq_accel_rev is not None else None,
            "status": "green" if qoq_accel_rev is not None and qoq_accel_rev > 0
                else "yellow" if qoq_accel_rev is not None and qoq_accel_rev > -0.05
                else "red",
            "comment": "양수 = 가속 · 음수 = 둔화",
        },
        {
            "key": "ev_ebitda", "label": "EV / EBITDA",
            "value": round(r["ev_ebitda"], 1) if r.get("ev_ebitda") else None,
            "status": _grade(r.get("ev_ebitda"), 15.0, 25.0, "lower"),
            "comment": "15 미만 합리적 · 25 이상 거품 가능",
        },
    ]

    return {
        "symbol": symbol.upper(),
        "snapshot": {
            "sector": r.get("sector"),
            "industry": r.get("industry"),
            "current_price": r.get("current_price"),
            "fifty_two_week_high": high_52,
            "fifty_two_week_low": low_52,
            "high_52w_dist_pct": round(high_dist * 100, 2) if high_dist is not None else None,
            "next_earnings_date": r.get("next_earnings_date"),
            "days_to_earnings": days_to_er,
            "long_business_summary": r.get("long_business_summary"),
            "korean_business_summary": r.get("korean_business_summary"),
        },
        "margins_latest": {
            "gross": round(gross_margin * 100, 2) if gross_margin is not None else None,
            "operating": round(op_margin * 100, 2) if op_margin is not None else None,
            "net": round(net_margin * 100, 2) if net_margin is not None else None,
            "fcf": round(fcf_margin * 100, 2) if fcf_margin is not None else None,
        },
        "valuation": {
            "forward_pe": r.get("forward_pe") if "forward_pe" in r else None,
            "ev_ebitda": r.get("ev_ebitda"),
            "ev_revenue": r.get("ev_revenue"),
            "price_to_book": r.get("price_to_book"),
            "price_to_sales": r.get("price_to_sales"),
        },
        "risk": {
            "beta": r.get("beta"),
            "institutional_pct": r.get("institutional_pct"),
            "insider_pct": r.get("insider_pct"),
            "short_ratio": r.get("short_ratio"),
        },
        "series": {
            "quarterly_revenue": r.get("quarterly_revenue") or [],
            "quarterly_eps": r.get("quarterly_eps") or [],
            "quarterly_net_income": r.get("quarterly_net_income") or [],
            "quarterly_operating_income": r.get("quarterly_operating_income") or [],
            "quarterly_gross_profit": r.get("quarterly_gross_profit") or [],
            "annual_revenue": r.get("annual_revenue") or [],
            "annual_net_income": r.get("annual_net_income") or [],
            "annual_eps": r.get("annual_eps") or [],
            "quarterly_ocf": r.get("quarterly_ocf") or [],
            "quarterly_fcf": r.get("quarterly_fcf") or [],
            "quarterly_total_debt": r.get("quarterly_total_debt") or [],
            "quarterly_equity": r.get("quarterly_equity") or [],
            "weekly_volume": weekly_volume,
        },
        "checklist": checklist,
        "fetched_at": r.get("fetched_at"),
    }


@router.post("/refresh")
async def refresh(
    target_date: date | None = None,
    dry_run: bool = True,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """수동 재선정 — 기본은 dry-run."""
    from scripts.longterm_monthly_pick import main as monthly_main

    if target_date is None:
        target_date = date.today()

    result = await monthly_main(target_date, dry_run=dry_run)
    return {
        "target_date": target_date.isoformat(),
        "dry_run": dry_run,
        "status": result.get("status"),
        "defensive": result.get("defensive"),
        "candidates_passed": result.get("candidates_passed"),
        "pick_count": len(result.get("picks", [])),
        "db_inserted": result.get("db_inserted", 0),
    }
