"""오늘 시장 상황 진단 — 매매 plan 결정 직전 자동 평가.

09:00 (log) 직후 호출 시 6개 시그널 종합 평가 + 권장 행동 반환.
사용자가 /trading 페이지 들어올 때 함께 fetch → 상단 진단 카드로 표시.

판정 로직:
  defensive (auto-trade 중단 권장): regime defensive 또는 5+ 시그널 trigger
  warning (사용자 review 후 진행):   3~4 시그널 trigger
  normal (auto-trade OK):            0~2 시그널 trigger

GET /api/market-diagnosis/today
GET /api/market-diagnosis/{date}
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.db.models import PickOutcome, SystemPickLog

router = APIRouter()


SignalLevel = Literal["normal", "warning", "danger"]


class SignalRow(BaseModel):
    key: str
    label_ko: str
    value: str  # 표시 텍스트
    raw: Any = None  # 원본 수치
    threshold_ko: str  # 임계치 설명
    level: SignalLevel  # normal | warning | danger
    note: str | None = None  # 보조 설명


class MarketDiagnosisResponse(BaseModel):
    diagnosis_date: date
    verdict: Literal["normal", "warning", "defensive"]
    verdict_ko: str
    verdict_summary: str  # 한 줄 진단
    recommendation: str  # 권장 행동
    auto_trade_advice: Literal["proceed", "review", "halt"]
    signal_count_triggered: int
    signal_count_total: int
    signals: list[SignalRow]
    possibilities: list[dict[str, str]]  # v10<v9일 때 가능한 시나리오 5개


def _build_possibilities(
    v10_count: int, v9_count: int, regime_mode: str
) -> list[dict[str, str]]:
    """v10 < v9 또는 v10 picks 적은 상황일 때 5가지 가능 시나리오."""
    if v10_count >= 5 and v9_count == 0:
        return []
    return [
        {
            "title": "1. 진짜 약세장",
            "state": "regime defensive",
            "example": "SPY -2%, VIX 30↑",
        },
        {
            "title": "2. 횡보장",
            "state": "regime neutral but momentum 약함",
            "example": "SPY ±0.5% 며칠 지속",
        },
        {
            "title": "3. 셋업 회전",
            "state": "regime OK but 새 셋업 미형성",
            "example": "강세장 일시 조정, base 형성 중",
        },
        {
            "title": "4. Auto-blacklist 누적",
            "state": "과거 손실 종목 많아 pool 축소",
            "example": "지난주 큰 손실 후",
        },
        {
            "title": "5. 섹터 cap에 막힘",
            "state": "반도체에 picks 몰려 다른 섹터 부재",
            "example": "모두 IT/Semi라 분산 강제로 못 통과",
        },
    ]


def _verdict_from_count(
    triggered: int, danger_count: int, regime_mode: str
) -> tuple[
    Literal["normal", "warning", "defensive"],
    Literal["proceed", "review", "halt"],
]:
    """판정 매트릭스.

    defensive: regime이 명시적 defensive이거나, danger 시그널 4+
               (regime + v3=0 + v10=0 + v9=0% + VIX>30 같이 강력한 동시 신호)
    warning:   trigger 2+ — 사용자 review 권장
    normal:    0~1 trigger
    """
    if regime_mode == "defensive":
        return "defensive", "halt"
    if danger_count >= 4:
        return "defensive", "halt"
    if triggered >= 2:
        return "warning", "review"
    return "normal", "proceed"


async def _diagnose(
    target_date: date, session: AsyncSession
) -> MarketDiagnosisResponse:
    from scanner.regime import evaluate_regime
    from scanner.stage2_daily_picks import get_market_context

    # 동기 yfinance 호출은 thread로
    regime = await asyncio.to_thread(evaluate_regime, target_date)
    market_ctx = await asyncio.to_thread(get_market_context, target_date)

    # system_pick_logs 조회 (오늘)
    stmt = select(
        SystemPickLog.system_id,
        SystemPickLog.symbol,
        SystemPickLog.score,
        SystemPickLog.score_meta,
    ).where(SystemPickLog.pick_date == target_date)
    rows = list((await session.execute(stmt)).all())

    v3_picks: list[str] = []
    v10_picks: list[str] = []
    v9_picks: list[str] = []
    scanner_picks_scored: list[tuple[str, float]] = []
    intraday_picks: list[str] = []
    for sys_id, symbol, score, meta in rows:
        source = (meta or {}).get("source") if isinstance(meta, dict) else None
        if sys_id == "v3":
            v3_picks.append(symbol)
        elif sys_id == "integrated":
            if source == "v9_fallback":
                v9_picks.append(symbol)
            else:
                v10_picks.append(symbol)
        elif sys_id == "scanner":
            scanner_picks_scored.append((symbol, float(score)))
        elif sys_id == "intraday":
            intraday_picks.append(symbol)

    v9_pct = (
        (len(v9_picks) / (len(v10_picks) + len(v9_picks)) * 100)
        if (len(v10_picks) + len(v9_picks)) > 0
        else 0.0
    )

    # auto-blacklist 종목 30일 lookback 계산 (alpha<-1% 2회+)
    blacklist: set[str] = set()
    try:
        lookback_start = target_date - timedelta(days=30)
        bl_stmt = (
            select(SystemPickLog.symbol, PickOutcome.alpha)
            .join(PickOutcome, PickOutcome.pick_log_id == SystemPickLog.id)
            .where(SystemPickLog.pick_date >= lookback_start)
            .where(SystemPickLog.pick_date < target_date)
            .where(PickOutcome.horizon_days == 5)
        )
        bl_rows = list((await session.execute(bl_stmt)).all())
        reject_count: dict[str, int] = {}
        for sym, alpha in bl_rows:
            if float(alpha) < -1.0:
                reject_count[sym] = reject_count.get(sym, 0) + 1
        blacklist = {s for s, c in reject_count.items() if c >= 2}
    except Exception:
        pass

    v9_blacklist_overlap = sorted(set(v9_picks) & blacklist)

    # VIX / SPY 갭
    vix_value = regime.diagnostics.get("vix_value") if regime.diagnostics else None
    spy_gap = market_ctx.get("SPY_gap_pct")

    # 시그널 평가
    signals: list[SignalRow] = []

    # 1. Regime score
    regime_level: SignalLevel = (
        "danger" if regime.mode == "defensive"
        else "warning" if regime.mode == "neutral"
        else "normal"
    )
    signals.append(SignalRow(
        key="regime",
        label_ko="시장 상태 점수",
        value=f"{regime.score:.0f}/15 ({regime.mode})",
        raw=regime.score,
        threshold_ko="<7 = 방어모드",
        level=regime_level,
        note=("long 진입 자동 차단" if regime.mode == "defensive"
              else "포지션 ×0.7 권장" if regime.mode == "neutral"
              else "풀 사이즈 OK"),
    ))

    # 2. v3 picks
    v3_n = len(v3_picks)
    signals.append(SignalRow(
        key="v3_picks",
        label_ko="v3 picks",
        value=f"{v3_n}",
        raw=v3_n,
        threshold_ko="0 = Stage 2 셋업 부재",
        level="danger" if v3_n == 0 else "warning" if v3_n < 3 else "normal",
        note=(
            "Stage 2 통과 종목 0 — 약세장 전조"
            if v3_n == 0
            else f"통과 종목: {', '.join(v3_picks[:5])}"
        ),
    ))

    # 3. v10 picks
    v10_n = len(v10_picks)
    signals.append(SignalRow(
        key="v10_picks",
        label_ko="v10 picks",
        value=f"{v10_n}",
        raw=v10_n,
        threshold_ko="<2 = v10 게이트 strict",
        level="danger" if v10_n == 0 else "warning" if v10_n < 2 else "normal",
        note=(
            "long_blocked 또는 셋업 부재"
            if v10_n == 0
            else f"통과: {', '.join(v10_picks[:5])}"
        ),
    ))

    # 4. v9 보충 비율
    signals.append(SignalRow(
        key="v9_ratio",
        label_ko="v9 보충 비율",
        value=f"{v9_pct:.0f}% ({len(v9_picks)}/{len(v10_picks) + len(v9_picks)})",
        raw=v9_pct,
        threshold_ko=">50% = v10 의존도 ↓",
        level="danger" if v9_pct > 50 else "warning" if v9_pct > 20 else "normal",
        note=(
            f"v9 보충: {', '.join(v9_picks[:5])}"
            if v9_picks
            else "v10이 top 5 충분"
        ),
    ))

    # 5. Auto-blacklist 종목이 v9에 등장
    signals.append(SignalRow(
        key="blacklist_in_v9",
        label_ko="블랙리스트 종목 v9 등장",
        value=", ".join(v9_blacklist_overlap) if v9_blacklist_overlap else "없음",
        raw=len(v9_blacklist_overlap),
        threshold_ko="1+ = 위험 신호",
        level="warning" if v9_blacklist_overlap else "normal",
        note=(
            "v9는 blacklist 미적용 — 손실 종목이 다시 추천"
            if v9_blacklist_overlap
            else None
        ),
    ))

    # 6. VIX
    if vix_value is not None and vix_value > 0:
        signals.append(SignalRow(
            key="vix",
            label_ko="VIX (변동성)",
            value=f"{vix_value:.1f}",
            raw=vix_value,
            threshold_ko=">25 = 변동성 ↑",
            level="danger" if vix_value > 30 else "warning" if vix_value > 25 else "normal",
            note=(
                "compression+expansion 둘 다 요구 (자동)"
                if vix_value > 25
                else "변동성 안정"
            ),
        ))
    else:
        signals.append(SignalRow(
            key="vix",
            label_ko="VIX (변동성)",
            value="—",
            raw=None,
            threshold_ko=">25 = 변동성 ↑",
            level="normal",
            note="VIX 데이터 미가용",
        ))

    # 7. SPY 갭
    if spy_gap is not None:
        signals.append(SignalRow(
            key="spy_gap",
            label_ko="SPY 시초가 갭",
            value=f"{spy_gap:+.2f}%",
            raw=spy_gap,
            threshold_ko="<-1% = 갭다운",
            level=("danger" if spy_gap < -1.0 else "warning" if spy_gap < 0
                   else "normal"),
            note=(
                "약세 갭다운 — 단타 진입 보수적"
                if spy_gap < -1.0
                else None
            ),
        ))

    triggered = sum(1 for s in signals if s.level in ("warning", "danger"))
    danger_count = sum(1 for s in signals if s.level == "danger")

    verdict, advice = _verdict_from_count(triggered, danger_count, regime.mode)

    verdict_ko_map = {
        "normal": "정상",
        "warning": "주의 (warning zone)",
        "defensive": "방어 (auto-trade 중단)",
    }
    summary_map = {
        "normal": "auto-trade 정상 진행",
        "warning": "auto-trade는 작동하되 사용자 review 권장",
        "defensive": "long 진입 자동 차단 — 매매 보류",
    }
    rec_map = {
        "proceed": (
            "✅ 정상: 매매 plan 그대로 진행. "
            "AUTO_TRADE_ENABLED=true 유지."
        ),
        "review": (
            "⚠️ 사용자 검토: /trading에서 워치리스트 직접 확인. "
            "단타는 ORB confirm 자동 진행, 스윙은 amount 입력 후 결정."
        ),
        "halt": (
            "🛑 매매 중단 권장: AUTO_TRADE_ENABLED=false 일시 설정. "
            "오늘은 종목 진입 보류, 보유 포지션의 stop/target만 유지."
        ),
    }

    return MarketDiagnosisResponse(
        diagnosis_date=target_date,
        verdict=verdict,
        verdict_ko=verdict_ko_map[verdict],
        verdict_summary=summary_map[verdict],
        recommendation=rec_map[advice],
        auto_trade_advice=advice,
        signal_count_triggered=triggered,
        signal_count_total=len(signals),
        signals=signals,
        possibilities=_build_possibilities(v10_n, len(v9_picks), regime.mode),
    )


@router.get("/today", response_model=MarketDiagnosisResponse)
async def get_today_diagnosis(
    session: AsyncSession = Depends(get_session),
) -> MarketDiagnosisResponse:
    return await _diagnose(date.today(), session)


@router.get("/{target_date}", response_model=MarketDiagnosisResponse)
async def get_diagnosis_by_date(
    target_date: date,
    session: AsyncSession = Depends(get_session),
) -> MarketDiagnosisResponse:
    return await _diagnose(target_date, session)
