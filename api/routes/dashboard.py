"""통합 대시보드 — /scanner의 정확도 + /picks의 운용 정보를 결합.

각 후보마다 자동으로:
  1. ATR(14) 기반 entry/stop/1R/2R 가격 계산
  2. account risk 0.5% 기준 주식 수 (qty) 계산
  3. 자연어 "왜 이 종목인가" 빌더 (백테스트 통계 + 시그널 + PEAD 반영)
  4. Tier (S/A/B/C) 분류 — 백테스트 검증된 강도 + 실적 단계로 가중
     다중 경로: Perfect (Score 6) / Stats (hit+avg) / Battle (큰 표본)
  5. ATR 기반 손절가 + 목표가 계산
  6. OHLC + MA20/50/200 차트 데이터 endpoint

GET /api/dashboard/today
GET /api/dashboard/strictness-levels
GET /api/dashboard/bars/{symbol}
"""

# reload trigger v7 — chart endpoint MA fix

from __future__ import annotations

import asyncio
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from api.db import get_session
from api.db.models import Bar
from api.routes.scanner import (
    DEFAULT_FILTER_PATH,
    SECTOR_MAP_PATH,
    _build_regime_status,
    _load_filter,
    _load_sectors,
)
from scripts.scan_momentum import (
    EARNINGS_CALENDAR_PATH,
    earnings_phase,
    evaluate_at_date,
    fetch_bars,
    list_symbols,
    load_earnings_calendar,
)
from signals.macro_regime import (
    compute_regime_state,
    load_macro_bars,
)

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Tier 임계값 (백테스트에서 도출, 다중 경로)
# Tier S Path 1: 시그널 완벽 정렬
TIER_S_PERFECT_MIN_SCORE = 6  # 모든 시그널 양수
TIER_S_PERFECT_MIN_HIT = 0.55  # 통계 약해도 score 6은 자체로 강함
TIER_S_PERFECT_MIN_N = 5

# Tier S Path 2: 통계적 강도
TIER_S_STATS_MIN_SCORE = 5
TIER_S_STATS_MIN_HIT = 0.65
TIER_S_STATS_MIN_AVG = 0.015  # 1.5%
TIER_S_STATS_MIN_N = 10

# Tier S Path 3: 압도적 표본 (battle-tested)
TIER_S_BATTLE_MIN_SCORE = 5
TIER_S_BATTLE_MIN_HIT = 0.70
TIER_S_BATTLE_MIN_N = 20

TIER_A_MIN_SCORE = 4
TIER_A_MIN_HIT = 0.55

TIER_B_MIN_SCORE = 3
TIER_B_MIN_HIT = 0.55

# 사이즈/리스크 디폴트
DEFAULT_EQUITY = 25000.0
DEFAULT_RISK_PER_TRADE = 0.005  # 0.5% account risk per trade
DEFAULT_ATR_PERIOD = 14
DEFAULT_ATR_STOP_MULT = 2.0


# Tier 엄격도 (1=매우 엄격, 5=매우 완화). 각 단계별 임계값.
# 5단계 디스크리트 — 6개 슬라이더 대신 사용자가 한 슬라이더로 전체 조정.
TIER_STRICTNESS_LEVELS: dict[int, dict[str, Any]] = {
    1: {  # 매우 엄격 — Tier S는 진정 최고 종목만
        "label": "매우 엄격",
        "s_stats_hit": 0.75, "s_stats_avg": 0.025, "s_stats_n": 20,
        "s_battle_hit": 0.75, "s_battle_n": 30,
        "s_perfect_hit": 0.65, "s_perfect_n": 10,
        "a_hit": 0.65, "a_n": 12,
        "b_hit": 0.65, "b_n": 8,
    },
    2: {  # 엄격
        "label": "엄격",
        "s_stats_hit": 0.70, "s_stats_avg": 0.020, "s_stats_n": 15,
        "s_battle_hit": 0.72, "s_battle_n": 25,
        "s_perfect_hit": 0.60, "s_perfect_n": 8,
        "a_hit": 0.60, "a_n": 10,
        "b_hit": 0.60, "b_n": 8,
    },
    3: {  # 표준 (기존 기본값)
        "label": "표준",
        "s_stats_hit": 0.65, "s_stats_avg": 0.015, "s_stats_n": 10,
        "s_battle_hit": 0.70, "s_battle_n": 20,
        "s_perfect_hit": 0.55, "s_perfect_n": 5,
        "a_hit": 0.55, "a_n": 8,
        "b_hit": 0.55, "b_n": 5,
    },
    4: {  # 완화 — 더 많은 후보
        "label": "완화",
        "s_stats_hit": 0.60, "s_stats_avg": 0.010, "s_stats_n": 8,
        "s_battle_hit": 0.65, "s_battle_n": 15,
        "s_perfect_hit": 0.50, "s_perfect_n": 4,
        "a_hit": 0.50, "a_n": 6,
        "b_hit": 0.50, "b_n": 4,
    },
    5: {  # 매우 완화
        "label": "매우 완화",
        "s_stats_hit": 0.55, "s_stats_avg": 0.005, "s_stats_n": 5,
        "s_battle_hit": 0.60, "s_battle_n": 10,
        "s_perfect_hit": 0.45, "s_perfect_n": 3,
        "a_hit": 0.45, "a_n": 4,
        "b_hit": 0.45, "b_n": 3,
    },
}


def compute_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """Wilder ATR. 마지막 값 반환. 데이터 부족 시 None."""
    if len(df) < period + 1:
        return None
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    if atr.empty or pd.isna(atr.iloc[-1]):
        return None
    return float(atr.iloc[-1])


class TradeLevels(BaseModel):
    entry: float
    stop: float
    target_1r: float
    target_2r: float
    risk_per_share: float
    risk_pct: float  # entry 대비
    qty: int
    position_value: float
    account_risk_dollar: float


class Reason(BaseModel):
    label: str
    detail: str
    polarity: str  # 'positive' | 'negative' | 'neutral'


class DashboardCandidate(BaseModel):
    rank: int
    tier: str  # 'S' | 'A' | 'B' | 'C'
    tier_path: str  # 통과 경로 (perfect/stats/battle/score+stats/post-pead/...)
    symbol: str
    sector: str | None
    earnings_phase: str
    earnings_next: str | None
    earnings_days: int | None
    close: float
    volume: int
    vol_vs_20d_avg: float | None
    signals: dict[str, int]
    total_score: int
    historical: dict[str, Any] | None
    levels: TradeLevels | None
    reasons: list[Reason]
    score_breakdown: dict[str, int]  # 시그널 카테고리별 분해


class DashboardResponse(BaseModel):
    as_of: str
    regime: dict[str, Any]
    n_candidates: int
    n_tier_s: int
    n_tier_a: int
    n_tier_b: int
    n_tier_c: int
    config: dict[str, Any]
    tiers: dict[str, list[DashboardCandidate]]


def classify_tier(
    total_score: int,
    historical: dict[str, Any] | None,
    earnings_phase_v: str,
    thresholds: dict[str, Any],
) -> tuple[str, str]:
    """Tier 분류 — strictness level별 임계값 적용.

    Returns: (tier, path_label) — perfect/stats/battle/score+stats/post-pead/high-score/wl-clean/wl-pead/watch
    """
    hit = historical.get("hit_rate", 0) if historical else 0
    avg = historical.get("avg_ret", 0) if historical else 0
    n = historical.get("n", 0) if historical else 0

    is_pre = earnings_phase_v == "pre"
    not_pre = not is_pre

    # ─── Tier S 다중 경로 ───────────────────────────────────────

    if (
        total_score >= TIER_S_PERFECT_MIN_SCORE
        and not_pre
        and historical
        and hit >= thresholds["s_perfect_hit"]
        and n >= thresholds["s_perfect_n"]
    ):
        return "S", "perfect"

    if (
        total_score >= TIER_S_STATS_MIN_SCORE
        and not_pre
        and historical
        and hit >= thresholds["s_stats_hit"]
        and avg >= thresholds["s_stats_avg"]
        and n >= thresholds["s_stats_n"]
    ):
        return "S", "stats"

    if (
        total_score >= TIER_S_BATTLE_MIN_SCORE
        and not_pre
        and historical
        and hit >= thresholds["s_battle_hit"]
        and n >= thresholds["s_battle_n"]
    ):
        return "S", "battle"

    # ─── Tier A ────────────────────────────────────────────────
    if total_score >= TIER_A_MIN_SCORE and not_pre:
        if historical and hit >= thresholds["a_hit"] and n >= thresholds["a_n"]:
            return "A", "score+stats"
        if earnings_phase_v == "post":
            return "A", "post-pead"
        if total_score >= 5 and historical and n >= thresholds["a_n"]:
            return "A", "high-score"

    # ─── Tier B ────────────────────────────────────────────────
    if (
        total_score >= TIER_B_MIN_SCORE
        and not_pre
        and historical
        and hit >= thresholds["b_hit"]
        and n >= thresholds["b_n"]
    ):
        return "B", "wl-clean"

    if total_score >= 3 and earnings_phase_v == "post" and historical and n >= thresholds["b_n"]:
        return "B", "wl-pead"

    return "C", "watch"


def categorize_signals(signals: dict[str, int]) -> dict[str, int]:
    """시그널을 카테고리별로 분해 (UI 표시용).

    - volume_strength : volume_trend
    - trend_alignment : ma_alignment + above_ma200
    - momentum        : rsi_bullish + macd
    - breakout        : breakout_20d
    """
    return {
        "volume_strength": max(0, signals.get("volume_trend", 0)),
        "trend_alignment": max(0, signals.get("ma_alignment", 0))
        + max(0, signals.get("above_ma200", 0)),
        "momentum": max(0, signals.get("rsi_bullish", 0))
        + max(0, signals.get("macd", 0)),
        "breakout": max(0, signals.get("breakout_20d", 0)),
        "negatives": -min(0, signals.get("ma_alignment", 0))
        - min(0, signals.get("above_ma200", 0))
        - min(0, signals.get("macd", 0)),  # 음수 시그널 합 (절대값)
    }


def build_reasons(
    cand: dict[str, Any],
    historical: dict[str, Any] | None,
    earnings_phase_v: str,
    earnings_next: str | None,
    atr: float | None,
) -> list[Reason]:
    """자연어 reason 빌더. 시그널 + 통계 + PEAD를 한국어로 설명."""
    reasons: list[Reason] = []
    s = cand["signals"]
    score = cand["total_score"]

    # 1. 시그널 점수 강도
    n_pos = sum(1 for v in s.values() if v > 0)
    if score >= 5:
        reasons.append(Reason(
            label="시그널 합의 매우 강함",
            detail=f"6개 시그널 중 {n_pos}개 동시 점화 — 정렬된 셋업",
            polarity="positive",
        ))
    elif score >= 4:
        reasons.append(Reason(
            label="시그널 합의 강함",
            detail=f"6개 시그널 중 {n_pos}개 점화",
            polarity="positive",
        ))
    elif score >= 3:
        reasons.append(Reason(
            label="시그널 보통",
            detail=f"6개 시그널 중 {n_pos}개 점화 — 보조 후보",
            polarity="neutral",
        ))

    # 2. 거래량
    rvol = cand.get("vol_vs_20d_avg")
    if rvol is not None:
        if rvol >= 2.0:
            reasons.append(Reason(
                label="거래량 폭증",
                detail=f"평균 대비 {rvol:.2f}× — 강한 관심 유입",
                polarity="positive",
            ))
        elif rvol >= 1.5:
            reasons.append(Reason(
                label="거래량 증가",
                detail=f"평균 대비 {rvol:.2f}× — 모멘텀 지지",
                polarity="positive",
            ))
        elif rvol < 0.7:
            reasons.append(Reason(
                label="거래량 부진",
                detail=f"평균 대비 {rvol:.2f}× — 시그널 신뢰도 낮음",
                polarity="negative",
            ))

    # 3. PEAD / earnings
    if earnings_phase_v == "pre":
        reasons.append(Reason(
            label="실적 발표 임박",
            detail=f"{earnings_next or ''} 발표 예정 — binary risk 회피 권장",
            polarity="negative",
        ))
    elif earnings_phase_v == "post":
        reasons.append(Reason(
            label="실적 직후 (PEAD)",
            detail="최근 발표 직후 — 본 시스템 백테스트 +2.56%/5d 알파 표본",
            polarity="positive",
        ))

    # 4. 시그널별 자연어
    if s.get("breakout_20d", 0) > 0:
        reasons.append(Reason(
            label="20일 신고가 돌파",
            detail="가격이 직전 20거래일 최고점을 갱신",
            polarity="positive",
        ))
    if s.get("ma_alignment", 0) > 0:
        reasons.append(Reason(
            label="이동평균 정배열",
            detail="5일 > 20일 > 60일 — 단기/중기 모두 상승 추세",
            polarity="positive",
        ))
    elif s.get("ma_alignment", 0) < 0:
        reasons.append(Reason(
            label="이동평균 역배열",
            detail="단기 평균이 중기 아래 — 추세 약화",
            polarity="negative",
        ))
    if s.get("above_ma200", 0) > 0:
        reasons.append(Reason(
            label="장기 추세 위",
            detail="200일선 위 — 장기적 상승 흐름",
            polarity="positive",
        ))
    elif s.get("above_ma200", 0) < 0:
        reasons.append(Reason(
            label="장기 추세 아래",
            detail="200일선 아래 — 장기 약세, 반등 시도",
            polarity="negative",
        ))
    if s.get("macd", 0) > 0:
        reasons.append(Reason(
            label="MACD 양전환",
            detail="단기 모멘텀 상승 전환",
            polarity="positive",
        ))
    elif s.get("macd", 0) < 0:
        reasons.append(Reason(
            label="MACD 음전환",
            detail="단기 모멘텀 하락 전환 — 주의",
            polarity="negative",
        ))
    if s.get("rsi_bullish", 0) > 0:
        reasons.append(Reason(
            label="RSI 상승 구간",
            detail="RSI 55~70 + 어제 대비 상승",
            polarity="positive",
        ))

    # 5. 백테스트 통계
    if historical:
        hit = historical.get("hit_rate", 0)
        avg = historical.get("avg_ret", 0)
        n = historical.get("n", 0)
        if hit >= 0.70 and n >= 8:
            reasons.append(Reason(
                label="백테스트 검증",
                detail=f"과거 {n}회 중 {int(hit*100)}% 흑자, 평균 {avg*100:+.2f}%",
                polarity="positive",
            ))
        elif hit >= 0.60 and n >= 8:
            reasons.append(Reason(
                label="WHITELIST 멤버",
                detail=f"과거 {n}회 중 {int(hit*100)}% 흑자, 평균 {avg*100:+.2f}%",
                polarity="positive",
            ))
    else:
        reasons.append(Reason(
            label="WHITELIST 외",
            detail="과거 검증 통계 없음 — 신중 진입",
            polarity="neutral",
        ))

    # 6. ATR (변동성)
    if atr is not None:
        atr_pct = atr / cand["close"] * 100
        if atr_pct >= 4.0:
            reasons.append(Reason(
                label="변동성 큼",
                detail=f"ATR 비율 {atr_pct:.1f}% — 빠른 손절 주의",
                polarity="negative",
            ))

    return reasons


def compute_levels(
    close: float,
    atr: float | None,
    equity: float,
    risk_per_trade: float,
    atr_mult: float,
) -> TradeLevels | None:
    """ATR 기반 entry/stop/1R/2R + qty 계산."""
    if atr is None or atr <= 0:
        return None
    entry = close
    stop = entry - atr_mult * atr
    if stop >= entry or stop <= 0:
        return None
    risk_per_share = entry - stop
    target_1r = entry + risk_per_share
    target_2r = entry + 2.0 * risk_per_share
    account_risk_dollar = equity * risk_per_trade
    qty = max(0, math.floor(account_risk_dollar / risk_per_share))
    position_value = qty * entry
    return TradeLevels(
        entry=round(entry, 2),
        stop=round(stop, 2),
        target_1r=round(target_1r, 2),
        target_2r=round(target_2r, 2),
        risk_per_share=round(risk_per_share, 2),
        risk_pct=round(risk_per_share / entry * 100, 2),
        qty=qty,
        position_value=round(position_value, 2),
        account_risk_dollar=round(account_risk_dollar, 2),
    )


async def build_dashboard(
    *,
    target_date: date | None = None,
    score_min: int = 2,
    earnings_mode: str = "pre_only",
    equity: float = DEFAULT_EQUITY,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    atr_mult: float = DEFAULT_ATR_STOP_MULT,
    tier_strictness: int = 3,
    top: int = 50,
) -> DashboardResponse:
    """대시보드 응답 빌더 — 라우터와 이메일 리포트에서 공유.

    FastAPI Query 파싱을 분리해 외부 스크립트(예: daily_email_report)에서
    동일한 결과를 직접 생성할 수 있도록 한다.
    """
    target = target_date
    thresholds = TIER_STRICTNESS_LEVELS.get(
        tier_strictness, TIER_STRICTNESS_LEVELS[3]
    )

    fpath = DEFAULT_FILTER_PATH
    filter_data = _load_filter(fpath)
    sectors = _load_sectors()
    earnings_data = (
        load_earnings_calendar(EARNINGS_CALENDAR_PATH) if earnings_mode != "off" else None
    )

    # 매크로 regime
    macro = await load_macro_bars()
    state = compute_regime_state(macro, fallback_when_missing=False)
    regime_status = _build_regime_status(macro, state, target)

    # 종목별 평가 — WHITELIST 사전 필터로 bars fetch 부담 90%↓ (515→~120)
    symbols = await list_symbols()
    if filter_data:
        symbols = [s for s in symbols if filter_data.get(s, {}).get("group") == "whitelist"]

    # bars fetch 병렬화 — 동시성 제한(16)으로 DB 보호하면서 18s→2s 수준
    sem = asyncio.Semaphore(16)

    async def _fetch(sym: str) -> tuple[str, pd.DataFrame]:
        async with sem:
            return sym, await fetch_bars(sym)

    bars_results = await asyncio.gather(*(_fetch(s) for s in symbols))

    raw_candidates: list[dict] = []
    bars_cache: dict[str, pd.DataFrame] = {}
    for sym, bars in bars_results:
        if bars.empty:
            continue
        bars_cache[sym] = bars
        result = evaluate_at_date(bars, target)
        if result is None:
            continue
        result["symbol"] = sym
        result["sector"] = sectors.get(sym)
        raw_candidates.append(result)

    raw_candidates.sort(key=lambda r: r["total_score"], reverse=True)

    # earnings phase + filter
    enriched: list[dict] = []
    for r in raw_candidates:
        if earnings_data:
            phase = earnings_phase(
                r["symbol"], date.fromisoformat(r["as_of"]), earnings_data, 5
            )
            sym_data = earnings_data.get(r["symbol"], {})
            r["earnings_phase"] = phase
            r["earnings_next"] = sym_data.get("next")
            try:
                if r["earnings_next"]:
                    r["earnings_days"] = (
                        date.fromisoformat(r["earnings_next"])
                        - date.fromisoformat(r["as_of"])
                    ).days
                else:
                    r["earnings_days"] = None
            except Exception:
                r["earnings_days"] = None
        else:
            r["earnings_phase"] = "clean"
            r["earnings_next"] = None
            r["earnings_days"] = None

        if earnings_mode == "exclude" and r["earnings_phase"] != "clean":
            continue
        if earnings_mode == "pre_only" and r["earnings_phase"] == "pre":
            continue
        if r["total_score"] < score_min:
            continue
        enriched.append(r)

    # 각 종목에 ATR + levels + reasons + tier 추가
    out_candidates: list[DashboardCandidate] = []
    for i, r in enumerate(enriched[:top], 1):
        sym = r["symbol"]
        bars = bars_cache.get(sym)
        atr = compute_atr(bars, period=DEFAULT_ATR_PERIOD) if bars is not None else None
        levels = compute_levels(r["close"], atr, equity, risk_per_trade, atr_mult)

        f_entry = filter_data.get(sym) if filter_data else None
        hist = (
            {k: f_entry[k] for k in ("n", "hit_rate", "avg_ret", "median_ret") if k in f_entry}
            if f_entry
            else None
        )

        tier, tier_path = classify_tier(
            r["total_score"], hist, r["earnings_phase"], thresholds
        )
        reasons = build_reasons(r, hist, r["earnings_phase"], r["earnings_next"], atr)
        breakdown = categorize_signals(r["signals"])

        out_candidates.append(
            DashboardCandidate(
                rank=i,
                tier=tier,
                tier_path=tier_path,
                symbol=sym,
                sector=r.get("sector"),
                earnings_phase=r["earnings_phase"],
                earnings_next=r.get("earnings_next"),
                earnings_days=r.get("earnings_days"),
                close=r["close"],
                volume=r["volume"],
                vol_vs_20d_avg=r.get("vol_vs_20d_avg"),
                signals=r["signals"],
                total_score=r["total_score"],
                historical=hist,
                levels=levels,
                reasons=reasons,
                score_breakdown=breakdown,
            )
        )

    # Tier 분류
    by_tier: dict[str, list[DashboardCandidate]] = {"S": [], "A": [], "B": [], "C": []}
    for c in out_candidates:
        by_tier[c.tier].append(c)

    as_of_str = (
        out_candidates[0].symbol  # placeholder, 실제론 candidates의 as_of 가 같음
        if out_candidates
        else (target.isoformat() if target else date.today().isoformat())
    )
    if enriched:
        as_of_str = enriched[0]["as_of"]

    return DashboardResponse(
        as_of=as_of_str,
        regime=regime_status.dict(),
        n_candidates=len(out_candidates),
        n_tier_s=len(by_tier["S"]),
        n_tier_a=len(by_tier["A"]),
        n_tier_b=len(by_tier["B"]),
        n_tier_c=len(by_tier["C"]),
        config={
            "score_min": score_min,
            "earnings_mode": earnings_mode,
            "equity": equity,
            "risk_per_trade": risk_per_trade,
            "atr_mult": atr_mult,
            "atr_period": DEFAULT_ATR_PERIOD,
            "tier_strictness": tier_strictness,
            "tier_thresholds": thresholds,
        },
        tiers=by_tier,
    )


@router.get("/today", response_model=DashboardResponse)
async def get_dashboard_today(
    target_date: str | None = Query(None),
    score_min: int = Query(2, ge=0, le=10),
    earnings_mode: str = Query("pre_only", pattern="^(off|exclude|pre_only)$"),
    equity: float = Query(DEFAULT_EQUITY, gt=0),
    risk_per_trade: float = Query(DEFAULT_RISK_PER_TRADE, gt=0, le=0.05),
    atr_mult: float = Query(DEFAULT_ATR_STOP_MULT, gt=0.5, le=5.0),
    tier_strictness: int = Query(3, ge=1, le=5, description="1=매우 엄격, 5=매우 완화"),
    top: int = Query(50, ge=1, le=200),
) -> DashboardResponse:
    target = (
        datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else None
    )
    return await build_dashboard(
        target_date=target,
        score_min=score_min,
        earnings_mode=earnings_mode,
        equity=equity,
        risk_per_trade=risk_per_trade,
        atr_mult=atr_mult,
        tier_strictness=tier_strictness,
        top=top,
    )


@router.get("/strictness-levels")
async def get_strictness_levels() -> dict[int, dict[str, Any]]:
    """5단계 strictness 임계값 메타데이터 (UI 표시용)."""
    return TIER_STRICTNESS_LEVELS


class BarPoint(BaseModel):
    time: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int


class ChartResponse(BaseModel):
    symbol: str
    bars: list[BarPoint]
    ma20: list[float | None]  # SMA(20), bars와 같은 길이
    ma50: list[float | None]
    ma200: list[float | None]
    levels: TradeLevels | None  # ATR 기반 entry/stop/target (현재 시점)


@router.get("/bars/{symbol}", response_model=ChartResponse)
async def get_bars(
    symbol: str,
    days: int = Query(120, ge=20, le=500, description="최근 N 거래일"),
    equity: float = Query(DEFAULT_EQUITY, gt=0),
    risk_per_trade: float = Query(DEFAULT_RISK_PER_TRADE, gt=0, le=0.05),
    atr_mult: float = Query(DEFAULT_ATR_STOP_MULT, gt=0.5, le=5.0),
) -> ChartResponse:
    """단일 종목 OHLC + MA + entry/stop/target 가격 (차트 시각화용)."""
    full_bars = await fetch_bars(symbol.upper())
    if full_bars.empty:
        raise HTTPException(404, f"No bars for {symbol}")

    # MA를 full bars로 계산 후 마지막 N개만 slice (MA200이 truncate되면 데이터 부족)
    ma20_full = full_bars["close"].rolling(20).mean()
    ma50_full = full_bars["close"].rolling(50).mean()
    ma200_full = full_bars["close"].rolling(200).mean()

    bars = full_bars.tail(days).copy()
    ma20 = ma20_full.tail(days)
    ma50 = ma50_full.tail(days)
    ma200 = ma200_full.tail(days)

    # ATR 기반 levels (마지막 시점 기준, 전체 데이터로 계산)
    atr = compute_atr(full_bars, period=DEFAULT_ATR_PERIOD)
    last_close = float(full_bars["close"].iloc[-1])
    levels = compute_levels(last_close, atr, equity, risk_per_trade, atr_mult)

    bar_points = [
        BarPoint(
            time=ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts.date()),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
        )
        for ts, row in bars.iterrows()
    ]

    def _list_or_none(s: pd.Series) -> list[float | None]:
        return [None if pd.isna(v) else float(v) for v in s]

    return ChartResponse(
        symbol=symbol.upper(),
        bars=bar_points,
        ma20=_list_or_none(ma20),
        ma50=_list_or_none(ma50),
        ma200=_list_or_none(ma200),
        levels=levels,
    )
