"""Stage 2 Daily Picks v2 — 단타·스윙 hybrid 종목 선정.

설계서: docs/SELECTION_CRITERIA.md (v2)

Tier 1 — Hard Gates (12개): G1~G12. 하나라도 fail 시 탈락.
Tier 2 — Score (100점):
  Block A (40) Trend & RS:    A1 RS Rating + A2 Multi-TF MOM + A3 Stage 2
  Block B (25) Catalyst & Vol: B1 RVOL + B2 Vol Surge + B3 Catalyst
  Block C (20) Setup:          C1 Pattern + C2 Pivot 근접 + C3 Base 기간
  Block D (15) Risk:           D1 RSI + D2 Beta + D3 ATR-RR + D4 Sector
  Penalty (-15까지): P1 거래량 부족 / P2 Climax / P3 Squeeze / P4 Extended / P5 RSI 과열

컷: ≥60점 (lenient: ≥40점) → Top 5 → 섹터 중복 압축 → Top 3 + 백업 2.

CLI:
    python -m scanner.stage2_daily_picks --date 2026-05-08
    python -m scanner.stage2_daily_picks                # 오늘
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import DailyPick, UniverseMember
from backtests.data_cache import get_bars
from scanner.benchmarks import (
    all_benchmark_symbols,
    get_benchmark_bars,
    market_uptrend_check,
    sector_etf_for,
)
from scanner.catalysts import CatalystKind, CatalystScore, aggregate_catalyst
from scanner.catalysts import nasdaq_earnings
from signals import SIGNAL_REGISTRY
from signals.atr import atr as atr_series
from signals.atr import atr_pct as atr_pct_series
from signals.relative_strength import rs_ibd_raw, rs_percentile, rs_vs_benchmark
from signals.rsi_oversold import _rsi
from signals.stage2_trend_template import trend_template_diagnostic, trend_template_pass
from signals.tight_flag_setup import detect_tight_flag

logger = logging.getLogger(__name__)

# ─────────── Gate 임계값 (v2 — 12개) ───────────

# G1 시장환경
GATE_QQQ_MIN_GAP_PCT = -1.0
# G3 유동성
GATE_AVG_DOLLAR_VOL_30D = 20_000_000
# G4 Float
GATE_FLOAT_MIN = 5_000_000
GATE_FLOAT_MAX = 5_000_000_000
# G5 가격대
GATE_PRICE_MIN = 5.0
GATE_PRICE_MAX = 500.0
# G7 halt — Phase 2 (현재 미구현)
# G8 스프레드
GATE_SPREAD_PCT = 0.3
# G9 ATR
GATE_ATR_PCT_MIN = 0.015
GATE_ATR_PCT_MAX = 0.12
# G10 거래량 sanity (lenient 모드)
GATE_DAILY_VOL_RATIO_MIN = 0.7
GATE_PREMARKET_DOLLAR_VOL = 5_000_000
# G11 Stage 2 Trend Template — 스윙만 적용
# G12 카탈리스트 — 단타만 적용

# 단타/스윙 분기 — ATR 기준
ATR_PCT_DAY_TRADE_MIN = 0.03  # ATR ≥ 가격의 3% → 단타 (변동성 큰 종목)

# ─────────── Score 만점 (v2 — 100점) ───────────

# Block 0 — Market Regime (15)
SCORE_B0_MAX = 15.0
# Block A — Trend & RS (25, was 40)
SCORE_A1_MAX = 10.0  # RS Rating IBD-style
SCORE_A2_MAX = 8.0   # Multi-timeframe MOM
SCORE_A3_MAX = 7.0   # Stage 2 Trend Template
# Block B — Catalyst & Volume (20, was 25)
SCORE_B1_MAX = 8.0   # RVOL
SCORE_B2_MAX = 4.0   # Vol Surge
SCORE_B3_MAX = 8.0   # Catalyst
# Block C — Setup (25, was 20) — Open Location + Compression 추가
SCORE_C1_MAX = 6.0   # Pattern
SCORE_C2_MAX = 4.0   # Pivot 근접
SCORE_C3_MAX = 4.0   # Base 기간
SCORE_C4_MAX = 5.0   # Open Location (신규)
SCORE_C5_MAX = 6.0   # Compression/Expansion (신규)
# Block D — Risk (15)
SCORE_D1_MAX = 5.0   # RSI Structure (구조 기반)
SCORE_D2_MAX = 3.0   # Beta
SCORE_D3_MAX = 3.0   # ATR-RR
SCORE_D4_MAX = 4.0   # Sector strength
# Penalty pool (max -15)
PENALTY_MAX = 15.0

# 카탈리스트 점수 매핑 (v3: max 8점)
SCORE_B3_MAP: dict[CatalystKind, float] = {
    CatalystKind.EARNINGS: 8.0,
    CatalystKind.FDA_MA: 6.0,
    CatalystKind.UPGRADE: 4.0,
    CatalystKind.NEWS: 1.0,
    CatalystKind.NONE: 0.0,
}

# 컷오프
SCORE_THRESHOLD = 60.0
SCORE_THRESHOLD_LENIENT = 40.0  # after-hours demo

POSITION_RISK_PCT = 0.005
DEFAULT_ACCOUNT_EQUITY = 25_000.0
MIN_RISK_PCT = 0.015

TOP_PICKS_COUNT = 3
BACKUP_PICKS_COUNT = 2


# ─────────── 데이터 클래스 ───────────


@dataclass
class CandidateMetrics:
    symbol: str
    sector: str | None = None
    market_cap: float | None = None
    float_shares: float | None = None
    prev_close: float | None = None
    premarket_open: float | None = None
    premarket_close: float | None = None
    premarket_high: float | None = None
    premarket_low: float | None = None
    premarket_dollar_vol: float = 0.0
    rvol: float = 0.0
    gap_pct: float = 0.0
    bid: float | None = None
    ask: float | None = None
    spread_pct: float | None = None
    yesterday_high: float | None = None


@dataclass
class GateResult:
    """v2 — 12 gates. ATR로 단타/스윙 분기 후 G11(스윙) 또는 G12(단타) 적용."""
    g1_market: bool = True
    g2_universe: bool = True
    g3_liquidity: bool = True
    g4_float: bool = True
    g5_price: bool = True
    g6_no_er_today: bool = True
    g7_no_recent_halt: bool = True
    g8_spread: bool = True
    g9_atr_range: bool = True
    g10_volume_sanity: bool = True
    g11_trend_template: bool = True  # 스윙만 적용 (단타는 자동 통과)
    g12_catalyst: bool = True         # 단타만 적용 (스윙은 자동 통과)
    horizon: str = "swing"  # "day" | "swing" — 분기 결과 기록

    def all_passed(self) -> bool:
        d = asdict(self)
        d.pop("horizon", None)
        return all(d.values())


@dataclass
class ScoreBreakdown:
    """v3 — 5 Block (0/A/B/C/D) + 6 Penalties. Total 0~100."""
    # Block 0 — Market Regime (15)
    b0_regime: float = 0.0
    # Block A — Trend & RS (25)
    a1_rs_rating: float = 0.0
    a2_mom_multi_tf: float = 0.0
    a3_stage2_strength: float = 0.0
    # Block B — Catalyst & Volume (20)
    b1_rvol: float = 0.0
    b2_vol_surge: float = 0.0
    b3_catalyst: float = 0.0
    # Block C — Setup (25)
    c1_pattern: float = 0.0
    c2_pivot_proximity: float = 0.0
    c3_base_duration: float = 0.0
    c4_open_location: float = 0.0
    c5_compression_expansion: float = 0.0
    # Block D — Risk (15)
    d1_rsi_structure: float = 0.0
    d2_beta: float = 0.0
    d3_atr_rr: float = 0.0
    d4_sector_strength: float = 0.0
    # Penalties (subtracted, max -15)
    p1_volume_deficit: float = 0.0
    p2_climax: float = 0.0
    p3_squeeze: float = 0.0
    p4_extended: float = 0.0
    p5_rsi_structure_violation: float = 0.0
    p6_open_location_risk: float = 0.0

    @property
    def block_0(self) -> float:
        return self.b0_regime

    @property
    def block_a(self) -> float:
        return self.a1_rs_rating + self.a2_mom_multi_tf + self.a3_stage2_strength

    @property
    def block_b(self) -> float:
        return self.b1_rvol + self.b2_vol_surge + self.b3_catalyst

    @property
    def block_c(self) -> float:
        return (self.c1_pattern + self.c2_pivot_proximity + self.c3_base_duration
                + self.c4_open_location + self.c5_compression_expansion)

    @property
    def block_d(self) -> float:
        return self.d1_rsi_structure + self.d2_beta + self.d3_atr_rr + self.d4_sector_strength

    @property
    def penalties_total(self) -> float:
        raw = (self.p1_volume_deficit + self.p2_climax + self.p3_squeeze
               + self.p4_extended + self.p5_rsi_structure_violation + self.p6_open_location_risk)
        return min(raw, PENALTY_MAX)

    @property
    def total(self) -> float:
        gross = self.block_0 + self.block_a + self.block_b + self.block_c + self.block_d
        return max(0.0, gross - self.penalties_total)


@dataclass
class PickRecord:
    symbol: str
    rank: int
    is_backup: bool
    metrics: CandidateMetrics
    gate: GateResult
    score: ScoreBreakdown
    catalyst: CatalystScore
    pivot: float
    stop: float
    target_1r: float
    target_2r: float
    risk_per_share: float
    position_size: int
    strategy_tag: str  # "day" | "swing"
    rationale: dict[str, Any] = field(default_factory=dict)


# ─────────── 시장 환경 (G1) ───────────


def get_market_context(target_date: date) -> dict[str, float]:
    """QQQ 프리마켓 갭 + 섹터 ETF 갭 — yfinance."""
    ctx = {}
    for sym in ("QQQ", "SPY", "SOXX", "XLK", "XLF", "XLV", "IWM"):
        gap = _gap_pct(sym, target_date)
        if gap is not None:
            ctx[f"{sym}_gap_pct"] = gap
    return ctx


def _gap_pct(symbol: str, target_date: date) -> float | None:
    """Premarket 갭 추정. yfinance fast_info의 lastPrice vs previousClose."""
    try:
        t = yf.Ticker(symbol)
        info = t.fast_info
        prev = float(info.get("previousClose") or info.get("regularMarketPreviousClose"))
        last = float(info.get("lastPrice") or info.get("regularMarketPrice"))
        if not prev or not last:
            return None
        return (last - prev) / prev * 100.0
    except Exception as exc:
        logger.debug("gap_pct failed for %s: %s", symbol, exc)
        return None


# ─────────── 종목 메트릭 수집 ───────────


def fetch_candidate_metrics(symbol: str, target_date: date) -> CandidateMetrics:
    """프리마켓 가격·거래량·갭·스프레드 등 — yfinance 1m + fast_info."""
    m = CandidateMetrics(symbol=symbol)

    try:
        t = yf.Ticker(symbol)
        info = t.fast_info
        m.market_cap = float(info.get("marketCap") or 0) or None
        m.prev_close = float(info.get("previousClose") or info.get("regularMarketPreviousClose") or 0) or None
        last = float(info.get("lastPrice") or info.get("regularMarketPrice") or 0)
        # bid/ask
        m.bid = float(info.get("bid") or 0) or None
        m.ask = float(info.get("ask") or 0) or None
        if m.bid and m.ask and m.bid > 0:
            m.spread_pct = (m.ask - m.bid) / ((m.ask + m.bid) / 2) * 100.0

        # float (info.floatShares는 deprecated하지만 fast_info에 없어 .info 사용)
        try:
            m.float_shares = float(t.info.get("floatShares") or 0) or None
            m.sector = t.info.get("sector")
        except Exception:
            pass

        if m.prev_close and last:
            m.gap_pct = (last - m.prev_close) / m.prev_close * 100.0
            m.premarket_close = last
    except Exception as exc:
        logger.debug("fast_info failed for %s: %s", symbol, exc)

    # 프리마켓 1분봉 (지난 1일 분봉, prepost=True)
    try:
        intraday = yf.download(
            symbol,
            period="2d",
            interval="1m",
            prepost=True,
            progress=False,
            auto_adjust=False,
        )
        if intraday is not None and not intraday.empty:
            if isinstance(intraday.columns, pd.MultiIndex):
                intraday.columns = intraday.columns.get_level_values(0)
            intraday.columns = [c.lower() for c in intraday.columns]

            # target_date 프리마켓: 04:00 ~ 09:30 ET
            ts = intraday.index
            if ts.tz is None:
                ts = ts.tz_localize("UTC")
            ts_et = ts.tz_convert("US/Eastern")
            mask = (
                (ts_et.date == target_date)
                & (ts_et.hour < 9)
                | ((ts_et.hour == 9) & (ts_et.minute < 30))
            )
            mask = mask & (ts_et.date == target_date)
            pm = intraday.loc[mask]
            if not pm.empty:
                m.premarket_open = float(pm["open"].iloc[0])
                m.premarket_high = float(pm["high"].max())
                m.premarket_low = float(pm["low"].min())
                m.premarket_dollar_vol = float((pm["close"] * pm["volume"]).sum())
    except Exception as exc:
        logger.debug("intraday fetch failed for %s: %s", symbol, exc)

    # 일봉 — 전일 고점, RVOL 산식용
    try:
        end = target_date.isoformat()
        start = (target_date - timedelta(days=60)).isoformat()
        daily = get_bars(symbol, start, end, "1d")
        if not daily.empty and len(daily) >= 21:
            m.yesterday_high = float(daily["high"].iloc[-1])
            avg_vol_20 = float(daily["volume"].iloc[-21:-1].mean())
            # 프리마켓 거래량 단독 vs 일평균 — 업계 표준은 시가총량 대비
            if avg_vol_20 > 0 and m.premarket_dollar_vol > 0 and m.premarket_close:
                pm_volume = m.premarket_dollar_vol / m.premarket_close
                m.rvol = pm_volume / avg_vol_20
    except Exception as exc:
        logger.debug("daily fetch failed for %s: %s", symbol, exc)

    return m


# ─────────── Gates ───────────


def evaluate_gates(
    m: CandidateMetrics,
    catalyst: CatalystScore,
    market_ctx: dict[str, float],
    target_date: date,
    *,
    after_hours_lenient: bool = False,
    daily_volume_ratio: float | None = None,
    daily_bars: pd.DataFrame | None = None,
    horizon: str = "swing",
    regime_score: float | None = None,
    trend_template_passed: bool | None = None,
) -> GateResult:
    """v3 — 12 Hard Gates.

    G1: regime_score ≥ 7 (Block 0 기반). 방어모드(<7)면 long 차단.
    G2: universe membership — 호출자가 보장 (default True)
    G3: 평균 30d 일거래대금 ≥ $20M
    G4: Float in [5M, 5B]
    G5: 가격 [$5, $500]
    G6: ER 당일 ±1일 아님
    G7: halt 이력 없음 (Phase 1: 항상 통과)
    G8: 스프레드 ≤ 0.3%
    G9: ATR% ∈ [1.5%, 12%]
    G10: 거래량 sanity (lenient 시 일봉 비율 ≥ 0.7× OR 프리마켓 ≥ $5M)
    G11: 스윙 후보면 Stage 2 Trend Template 통과
    G12: 단타 후보면 카탈리스트 존재
    """
    g = GateResult(horizon=horizon)

    # G1 — Regime
    if regime_score is None:
        # 폴백: QQQ 갭 기반 (Phase 1 backward-compat)
        qqq_gap = market_ctx.get("QQQ_gap_pct", 0.0)
        g.g1_market = qqq_gap > GATE_QQQ_MIN_GAP_PCT
    else:
        g.g1_market = regime_score >= 7.0

    # G2 — Universe (caller 보장)
    g.g2_universe = True

    # G3 — Liquidity (avg 30d dollar volume)
    if daily_bars is not None and len(daily_bars) >= 30:
        avg_dollar_vol = float((daily_bars["close"] * daily_bars["volume"]).iloc[-30:].mean())
        g.g3_liquidity = avg_dollar_vol >= GATE_AVG_DOLLAR_VOL_30D

    # G4 — Float
    if m.float_shares is None:
        g.g4_float = True  # 데이터 없으면 통과
    else:
        g.g4_float = GATE_FLOAT_MIN <= m.float_shares <= GATE_FLOAT_MAX

    # G5 — Price
    price = m.premarket_close or m.prev_close
    if price is None:
        g.g5_price = True
    else:
        g.g5_price = GATE_PRICE_MIN <= price <= GATE_PRICE_MAX

    # G6 — ER
    g.g6_no_er_today = not nasdaq_earnings.is_er_day(m.symbol, target_date)

    # G7 — Halt (Phase 1: 항상 통과, 데이터 미연결)
    g.g7_no_recent_halt = True

    # G8 — Spread
    g.g8_spread = (m.spread_pct is None) or (m.spread_pct <= GATE_SPREAD_PCT)

    # G9 — ATR range
    if daily_bars is not None and len(daily_bars) >= 15:
        try:
            apct = float(atr_pct_series(daily_bars).iloc[-1])
            g.g9_atr_range = GATE_ATR_PCT_MIN <= apct <= GATE_ATR_PCT_MAX
        except Exception:
            g.g9_atr_range = True
    else:
        g.g9_atr_range = True

    # G10 — Volume sanity
    if after_hours_lenient and m.premarket_dollar_vol == 0:
        if daily_volume_ratio is not None and daily_volume_ratio < GATE_DAILY_VOL_RATIO_MIN:
            g.g10_volume_sanity = False
        else:
            g.g10_volume_sanity = True
    else:
        g.g10_volume_sanity = m.premarket_dollar_vol >= GATE_PREMARKET_DOLLAR_VOL

    # G11 — Stage 2 Trend Template (스윙만)
    if horizon == "swing":
        if trend_template_passed is None and daily_bars is not None and len(daily_bars) >= 252:
            trend_template_passed = bool(trend_template_pass(daily_bars).iloc[-1])
        g.g11_trend_template = bool(trend_template_passed) if trend_template_passed is not None else True
    else:
        g.g11_trend_template = True  # 단타는 자동 통과

    # G12 — Catalyst (단타만)
    if horizon == "day":
        if after_hours_lenient:
            g.g12_catalyst = True  # paper 단계 완화
        else:
            g.g12_catalyst = catalyst.score > 0
    else:
        g.g12_catalyst = True  # 스윙은 자동 통과

    return g


# ─────────── Scores ───────────


def evaluate_scores(
    m: CandidateMetrics,
    catalyst: CatalystScore,
    market_ctx: dict[str, float],
    is_whitelist: bool,
    daily_bars: pd.DataFrame | None,
    intraday_bars: pd.DataFrame | None,
    *,
    regime_score: float = 0.0,
    rs_percentile_value: float | None = None,
    benchmark_bars: pd.DataFrame | None = None,
    pivot_price: float | None = None,
    open_price: float | None = None,
) -> tuple[ScoreBreakdown, dict[str, Any]]:
    """v3 — 5 Block + 6 Penalty 평가. ScoreBreakdown + rationale 반환."""
    from signals.compression_expansion import detect_compression_expansion
    from signals.open_location import compute_open_location
    from signals.relative_strength import rs_vs_benchmark
    from signals.rsi_structure import detect_rsi_structure

    s = ScoreBreakdown()
    rationale: dict[str, Any] = {
        "gap_pct": m.gap_pct,
        "rvol": m.rvol,
        "tight_flag": False,
        "breakout_20d": False,
        "near_52w_high": False,
        "sector_etf": None,
        "sector_etf_gap": None,
        "sector_aligned": None,
        "is_whitelist": is_whitelist,
        "catalyst_kind": catalyst.primary_kind.value,
        "rsi_14": None,
        "avg_volume_20d": None,
        "last_volume": None,
        "volume_vs_avg": None,
        # v3 신규
        "regime_score": regime_score,
        "rs_percentile": rs_percentile_value,
        "rs_1m": None,
        "rs_3m": None,
        "rs_6m": None,
        "stage2_pass": False,
        "compression": False,
        "expansion": False,
        "compression_ratio": None,
        "expansion_ratio": None,
        "open_location_above_prev_high": False,
        "open_location_above_pivot": False,
        "rsi_structure_grade": "neutral",
        "rsi_structure_notes": "",
    }

    # 일봉 기반 RSI + 거래량 비율
    if daily_bars is not None and len(daily_bars) >= 20:
        try:
            rsi_series = _rsi(daily_bars["close"], 14)
            rationale["rsi_14"] = float(rsi_series.iloc[-1])
        except Exception:
            pass
        try:
            avg_vol = float(daily_bars["volume"].iloc[-21:-1].mean())
            last_vol = float(daily_bars["volume"].iloc[-1])
            rationale["avg_volume_20d"] = avg_vol
            rationale["last_volume"] = last_vol
            if avg_vol > 0:
                rationale["volume_vs_avg"] = last_vol / avg_vol
        except Exception:
            pass

    # ─── Block 0: Market Regime ───
    s.b0_regime = float(min(regime_score, SCORE_B0_MAX))

    # ─── Block A: Trend & RS (25) ───
    # A1 RS Rating (10) — percentile 1~99 → 0~10
    if rs_percentile_value is not None:
        if rs_percentile_value >= 85:
            s.a1_rs_rating = SCORE_A1_MAX
        elif rs_percentile_value >= 70:
            s.a1_rs_rating = 7.0
        elif rs_percentile_value >= 50:
            s.a1_rs_rating = 4.0
        else:
            s.a1_rs_rating = 0.0
    # A2 Multi-TF MOM (8) — 1m/3m/6m all positive vs SPY
    if benchmark_bars is not None and daily_bars is not None:
        positive_count = 0
        for lb, key in [(21, "rs_1m"), (63, "rs_3m"), (126, "rs_6m")]:
            try:
                rs_v = rs_vs_benchmark(daily_bars, benchmark_bars, lb)
                rationale[key] = round(rs_v, 4) if rs_v is not None else None
                if rs_v is not None and rs_v > 1.0:
                    positive_count += 1
            except Exception:
                pass
        if positive_count == 3:
            s.a2_mom_multi_tf = SCORE_A2_MAX
        elif positive_count == 2:
            s.a2_mom_multi_tf = 5.0
        elif positive_count == 1:
            s.a2_mom_multi_tf = 2.0
    # A3 Stage 2 Trend Template (7)
    if daily_bars is not None and len(daily_bars) >= 252:
        try:
            tt_pass = bool(trend_template_pass(daily_bars).iloc[-1])
            rationale["stage2_pass"] = tt_pass
            tt_diag = trend_template_diagnostic(daily_bars)
            tt_count = sum(1 for v in tt_diag.values() if v)
            if tt_pass:
                s.a3_stage2_strength = SCORE_A3_MAX
            elif tt_count >= 6:
                s.a3_stage2_strength = 4.0
            else:
                s.a3_stage2_strength = 0.0
        except Exception:
            pass

    # ─── Block B: Catalyst & Volume (20) ───
    # B1 RVOL (8): min(8, 1.5×log2(RVOL+1))
    effective_rvol = m.rvol
    if m.rvol == 0 and rationale.get("volume_vs_avg") is not None:
        effective_rvol = max(0.01, rationale["volume_vs_avg"])
    s.b1_rvol = min(SCORE_B1_MAX, 1.5 * math.log2(effective_rvol + 1.0))
    s.b1_rvol = max(0.0, s.b1_rvol)
    # B2 Volume Surge (4)
    vr = rationale.get("volume_vs_avg")
    if vr is not None:
        if vr >= 2.0:
            s.b2_vol_surge = SCORE_B2_MAX
        elif vr >= 1.3:
            s.b2_vol_surge = 2.0
        else:
            s.b2_vol_surge = 0.0
    # B3 Catalyst kind (8)
    s.b3_catalyst = SCORE_B3_MAP.get(catalyst.primary_kind, 0.0)

    # ─── Block C: Setup Quality (25) ───
    # C1 Pattern (6)
    pattern_score = 0.0
    if intraday_bars is not None and len(intraday_bars) >= 12:
        ok, _tightness = detect_tight_flag(intraday_bars, len(intraday_bars) - 1, n=6)
        rationale["tight_flag"] = ok
        if ok:
            pattern_score = max(pattern_score, 6.0)
    if daily_bars is not None and len(daily_bars) >= 21:
        breakout = int(SIGNAL_REGISTRY["breakout_20d"].evaluate(daily_bars).iloc[-1])
        rationale["breakout_20d"] = breakout == 1
        if breakout == 1:
            pattern_score = max(pattern_score, 4.0)
        near_high = int(SIGNAL_REGISTRY["near_52w_high"].evaluate(daily_bars).iloc[-1])
        rationale["near_52w_high"] = near_high == 1
        if near_high == 1:
            pattern_score = max(pattern_score, 2.0)
    s.c1_pattern = min(pattern_score, SCORE_C1_MAX)

    # C2 Pivot proximity (4)
    if pivot_price and m.premarket_close:
        dist_pct = abs(m.premarket_close - pivot_price) / pivot_price
        if dist_pct <= 0.005:
            s.c2_pivot_proximity = SCORE_C2_MAX
        elif dist_pct <= 0.02:
            s.c2_pivot_proximity = 3.0
        elif dist_pct <= 0.05:
            s.c2_pivot_proximity = 1.0
        else:
            s.c2_pivot_proximity = 0.0  # extended는 P4로

    # C3 Base duration (4) — 직전 NR 컨솔 일수 (간단 정의: range가 평균의 70% 이하인 연속일수)
    if daily_bars is not None and len(daily_bars) >= 30:
        try:
            recent_range = (daily_bars["high"] - daily_bars["low"]) / daily_bars["close"]
            avg_range = recent_range.iloc[-30:].mean()
            nr_days = 0
            for i in range(len(recent_range) - 1, -1, -1):
                if recent_range.iloc[i] <= avg_range * 0.7:
                    nr_days += 1
                else:
                    break
            if 3 <= nr_days <= 20:
                s.c3_base_duration = SCORE_C3_MAX
            elif 1 <= nr_days <= 2:
                s.c3_base_duration = 2.0
            elif 21 <= nr_days <= 50:
                s.c3_base_duration = 3.0
        except Exception:
            pass

    # C4 Open Location (5) — 신규
    open_loc = compute_open_location(
        open_price=open_price or m.premarket_open or m.premarket_close,
        pivot_price=pivot_price,
        prev_high=m.yesterday_high,
        prev_low=None,  # daily_bars에서 추출
    )
    if daily_bars is not None and len(daily_bars) >= 1:
        try:
            prev_low = float(daily_bars["low"].iloc[-1])
            open_loc = compute_open_location(
                open_price=open_price or m.premarket_open or m.premarket_close,
                pivot_price=pivot_price,
                prev_high=m.yesterday_high,
                prev_low=prev_low,
            )
        except Exception:
            pass
    s.c4_open_location = open_loc.score
    rationale["open_location_above_prev_high"] = open_loc.above_prev_high
    rationale["open_location_above_pivot"] = open_loc.above_pivot

    # C5 Compression / Expansion (6) — 신규
    if daily_bars is not None and len(daily_bars) >= 45:
        try:
            ce = detect_compression_expansion(daily_bars)
            s.c5_compression_expansion = ce.score
            rationale["compression"] = ce.is_compression
            rationale["expansion"] = ce.is_expansion
            rationale["compression_ratio"] = round(ce.compression_ratio, 3)
            rationale["expansion_ratio"] = round(ce.expansion_ratio, 3)
        except Exception:
            pass

    # ─── Block D: Risk & Execution (15) ───
    # D1 RSI Structure (5) — 절대값 → 구조
    rsi_struct_result = None
    if daily_bars is not None and len(daily_bars) >= 30:
        try:
            rsi_struct_result = detect_rsi_structure(daily_bars)
            s.d1_rsi_structure = min(rsi_struct_result.score, SCORE_D1_MAX)
            rationale["rsi_structure_grade"] = rsi_struct_result.grade
            rationale["rsi_structure_notes"] = rsi_struct_result.notes
        except Exception:
            pass

    # D2 Beta sweet spot (3) — yfinance에서 beta 추정 (없으면 0)
    # 간단화: SPY 대비 일봉 회귀로 베타 산출
    if daily_bars is not None and benchmark_bars is not None and len(daily_bars) >= 60:
        try:
            stock_ret = daily_bars["close"].pct_change().iloc[-60:]
            bench_ret = benchmark_bars["close"].pct_change().iloc[-60:]
            common_idx = stock_ret.index.intersection(bench_ret.index)
            if len(common_idx) >= 30:
                cov = stock_ret.loc[common_idx].cov(bench_ret.loc[common_idx])
                var = bench_ret.loc[common_idx].var()
                if var > 0:
                    beta = cov / var
                    if 1.0 <= beta <= 2.0:
                        s.d2_beta = SCORE_D2_MAX
                    elif 0.7 <= beta <= 2.5:
                        s.d2_beta = 2.0
                    else:
                        s.d2_beta = 1.0
        except Exception:
            pass

    # D3 ATR-RR (3) — (피벗-진입)/ATR. 진입은 premarket_close, 피벗 정보 있을 때만
    if pivot_price and m.premarket_close and daily_bars is not None and len(daily_bars) >= 15:
        try:
            atr_val = float(atr_series(daily_bars).iloc[-1])
            if atr_val > 0:
                ratio = abs(pivot_price - m.premarket_close) / atr_val
                if ratio >= 3:
                    s.d3_atr_rr = SCORE_D3_MAX
                elif ratio >= 2:
                    s.d3_atr_rr = 2.0
                elif ratio >= 1:
                    s.d3_atr_rr = 1.0
        except Exception:
            pass

    # D4 Sector strength (4)
    if m.sector and m.gap_pct:
        sector_etf = sector_etf_for(m.sector)
        sector_gap = market_ctx.get(f"{sector_etf}_gap_pct") if sector_etf else None
        rationale["sector_etf"] = sector_etf
        rationale["sector_etf_gap"] = sector_gap
        if sector_gap is not None:
            same_sign = (m.gap_pct > 0 and sector_gap > 0) or (m.gap_pct < 0 and sector_gap < 0)
            rationale["sector_aligned"] = same_sign
            # 단순화: same_sign + ETF 갭 강한지로 판단
            if same_sign and abs(sector_gap) >= 0.5:
                s.d4_sector_strength = SCORE_D4_MAX
            elif same_sign:
                s.d4_sector_strength = 2.0
            else:
                s.d4_sector_strength = 0.0

    # ─── Penalties (max -15) ───
    # P1 거래량 부족 (-3): vol_ratio < 0.7
    if rationale.get("volume_vs_avg") is not None and rationale["volume_vs_avg"] < 0.7:
        s.p1_volume_deficit = 3.0
    # P2 Climax: RSI 85+ + 거래량 climax → rsi_struct이 처리. 동일 신호 재사용
    if rsi_struct_result and rsi_struct_result.is_climax:
        s.p2_climax = 4.0
    # P3 Squeeze: Phase 2 (Short Interest 데이터 없음) — 0 유지
    s.p3_squeeze = 0.0
    # P4 Pivot extended: 가격이 피벗 +5% 이상
    if pivot_price and m.premarket_close and pivot_price > 0:
        ext_pct = (m.premarket_close - pivot_price) / pivot_price
        if ext_pct > 0.05:
            s.p4_extended = 3.0
    # P5 RSI structure violation: bearish divergence OR climax
    if rsi_struct_result and (rsi_struct_result.has_bearish_divergence or rsi_struct_result.is_climax):
        s.p5_rsi_structure_violation = 3.0
    # P6 Open Location 위험 (gap-and-fail)
    if open_loc.gap_and_fail_risk:
        s.p6_open_location_risk = 2.0

    return s, rationale


# ─────────── 메타데이터 자동 산출 ───────────


def compute_pick_metadata(
    m: CandidateMetrics,
    daily_bars: pd.DataFrame | None,
    intraday_bars: pd.DataFrame | None,
    account_equity: float,
) -> tuple[float, float, float, float, float, int, str]:
    """피벗·손절·1R/2R·사이즈·전략 태그 산출.

    피벗 = max(전일고점, PMH)
    손절 = max(VWAP, 컨솔 저점, 전일종가) 중 피벗에 가장 가까운 값. 단 피벗 대비 ≥1.5% 차이.
    """
    pivot_candidates = [v for v in (m.yesterday_high, m.premarket_high) if v]
    if not pivot_candidates:
        pivot = m.premarket_close or 0.0
    else:
        pivot = max(pivot_candidates)

    # 손절 후보들
    stop_candidates: list[float] = []
    if m.prev_close:
        stop_candidates.append(float(m.prev_close))
    if intraday_bars is not None and not intraday_bars.empty:
        # 직전 30분 저점
        stop_candidates.append(float(intraday_bars["low"].tail(30).min()))
        # session VWAP
        from signals.vwap_reclaim import session_vwap

        try:
            vwap = float(session_vwap(intraday_bars).iloc[-1])
            stop_candidates.append(vwap)
        except Exception:
            pass

    # 피벗 아래에서, 피벗에 가장 가까운 후보 (단 최소 1.5% 차이)
    min_stop_distance = pivot * MIN_RISK_PCT
    valid_stops = [s for s in stop_candidates if s < pivot - min_stop_distance and s > 0]
    if valid_stops:
        stop = max(valid_stops)
    else:
        stop = pivot * (1 - MIN_RISK_PCT)

    risk_per_share = pivot - stop
    target_1r = pivot + risk_per_share
    target_2r = pivot + 2 * risk_per_share

    if risk_per_share > 0:
        size = math.floor((account_equity * POSITION_RISK_PCT) / risk_per_share)
    else:
        size = 0

    # 태그: ATR 기반으로 변동성 큰 종목은 day, 작은 종목은 swing 기본
    strategy_tag = "day"
    if daily_bars is not None and len(daily_bars) >= 14:
        atr_pct = float(((daily_bars["high"] - daily_bars["low"]) / daily_bars["close"]).tail(14).mean())
        if atr_pct < 0.02:
            strategy_tag = "swing"

    return pivot, stop, target_1r, target_2r, risk_per_share, size, strategy_tag


# ─────────── 섹터 중복 압축 (개선안 ④) ───────────


def compress_by_sector(picks: list[PickRecord]) -> list[PickRecord]:
    """같은 섹터 종목이 둘 이상이면 점수 높은 것만 top, 나머지는 backup으로 강등."""
    seen_sectors: set[str] = set()
    top: list[PickRecord] = []
    demoted: list[PickRecord] = []
    for p in sorted(picks, key=lambda x: x.score.total, reverse=True):
        sector_key = p.metrics.sector or "_unknown"
        if sector_key in seen_sectors:
            demoted.append(p)
        else:
            seen_sectors.add(sector_key)
            top.append(p)
        if len(top) >= TOP_PICKS_COUNT:
            break

    # backup: 점수 순 다음 후보 + 섹터 중복으로 강등된 것 합쳐서 상위 BACKUP_PICKS_COUNT
    remaining = [p for p in picks if p not in top]
    backup = sorted(remaining, key=lambda x: x.score.total, reverse=True)[:BACKUP_PICKS_COUNT]

    # rank 부여
    for i, p in enumerate(top, start=1):
        p.rank = i
        p.is_backup = False
    for i, p in enumerate(backup, start=TOP_PICKS_COUNT + 1):
        p.rank = i
        p.is_backup = True

    return top + backup


# ─────────── 메인 파이프라인 ───────────


def _auto_detect_after_hours() -> bool:
    """US/Eastern 기준 04:00~16:00 평일이면 strict, 그 외(주말·장 마감 후)면 lenient.

    yfinance 프리마켓 데이터는 04:00 ET부터 활성화되고 16:00 ET 정규장 마감 후엔 stale.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return False
    now_et = datetime.now(ZoneInfo("US/Eastern"))
    if now_et.weekday() >= 5:  # 주말
        return True
    minutes = now_et.hour * 60 + now_et.minute
    return not (240 <= minutes < 960)  # 04:00 ~ 16:00 ET 외


async def run_daily_picks(
    session: AsyncSession,
    target_date: date,
    account_equity: float = DEFAULT_ACCOUNT_EQUITY,
    *,
    after_hours_lenient: bool | None = None,
    day_mode_enabled: bool = False,
) -> list[PickRecord]:
    """v3 — Block 0 Regime + 5-block + 6-penalty + horizon (swing default)."""
    from signals.atr import atr_pct as _atr_pct
    from signals.relative_strength import rs_ibd_raw, rs_percentile
    from scanner.regime import evaluate_regime

    if after_hours_lenient is None:
        after_hours_lenient = _auto_detect_after_hours()
        logger.info("after_hours_lenient auto-detected: %s", after_hours_lenient)

    # 1) Block 0 — Market Regime
    regime = evaluate_regime(target_date)
    logger.info("Regime: score=%.1f mode=%s", regime.score, regime.mode)
    if regime.long_blocked():
        logger.warning(
            "Defensive regime (score=%.1f) — long candidates blocked. "
            "Mean-reversion oversold only.", regime.score
        )

    # 2) Universe 로드
    stmt = select(UniverseMember).where(
        UniverseMember.enabled == True,  # noqa: E712
        UniverseMember.source != "blacklist",
    )
    result = await session.execute(stmt)
    members = list(result.scalars().all())
    if not members:
        logger.warning("Universe is empty — Stage 1을 먼저 실행하세요.")
        return []

    whitelist_symbols = {m.symbol for m in members if m.source == "score5_whitelist"}
    candidates = sorted({m.symbol for m in members})

    # 3) 시장 환경 + 벤치마크 fetch (RS, beta 계산용)
    market_ctx = get_market_context(target_date)
    logger.info("Market context: %s", market_ctx)
    spy_bars = get_benchmark_bars("SPY", lookback_days=400)

    # 4) RS Rating universe pool — 모든 universe + SPY/QQQ/IWM raw RS 모음
    end_iso = target_date.isoformat()
    start_iso = (target_date - timedelta(days=400)).isoformat()
    rs_pool: dict[str, float | None] = {}
    daily_bars_cache: dict[str, pd.DataFrame] = {}
    for sym in candidates + ["SPY", "QQQ", "IWM"]:
        try:
            bars = get_bars(sym, start_iso, end_iso, "1d")
            daily_bars_cache[sym] = bars
            if len(bars) >= 252:
                rs_raw = float(rs_ibd_raw(bars).iloc[-1])
                rs_pool[sym] = rs_raw if pd.notna(rs_raw) else None
            else:
                rs_pool[sym] = None
        except Exception:
            rs_pool[sym] = None
    peer_raws = [v for v in rs_pool.values() if v is not None]

    # 5) 후보별 메트릭 + Gate + Score
    picks: list[PickRecord] = []
    threshold = SCORE_THRESHOLD_LENIENT if after_hours_lenient else SCORE_THRESHOLD

    for symbol in candidates:
        try:
            m = fetch_candidate_metrics(symbol, target_date)
            catalyst = aggregate_catalyst(symbol, target_date)
            daily_bars = daily_bars_cache.get(symbol)

            # 일봉 거래량 비율 (G10 lenient + B2)
            daily_vol_ratio: float | None = None
            if daily_bars is not None and len(daily_bars) >= 21:
                try:
                    avg_vol = float(daily_bars["volume"].iloc[-21:-1].mean())
                    last_vol = float(daily_bars["volume"].iloc[-1])
                    daily_vol_ratio = last_vol / avg_vol if avg_vol > 0 else None
                except Exception:
                    pass

            # Horizon 분기 (default = swing, day_mode_enabled + ATR ≥ 5%면 day)
            horizon = "swing"
            if day_mode_enabled and daily_bars is not None and len(daily_bars) >= 15:
                try:
                    apct = float(_atr_pct(daily_bars).iloc[-1])
                    if apct >= 0.05:
                        horizon = "day"
                except Exception:
                    pass

            # Stage 2 Trend Template 미리 계산 (G11)
            tt_pass: bool | None = None
            if daily_bars is not None and len(daily_bars) >= 252:
                try:
                    tt_pass = bool(trend_template_pass(daily_bars).iloc[-1])
                except Exception:
                    pass

            # G1: 방어모드면 long 차단
            if regime.long_blocked():
                # 평균회귀 후보만 통과: RSI<30 + intent로 좀 단순화 — 일단 모두 차단
                # (Phase 2: oversold reversal candle 검출 로직 추가)
                logger.debug("%s blocked by defensive regime", symbol)
                continue

            gate = evaluate_gates(
                m, catalyst, market_ctx, target_date,
                after_hours_lenient=after_hours_lenient,
                daily_volume_ratio=daily_vol_ratio,
                daily_bars=daily_bars,
                horizon=horizon,
                regime_score=regime.score,
                trend_template_passed=tt_pass,
            )
            if not gate.all_passed():
                logger.debug("%s gate fail: %s", symbol, asdict(gate))
                continue

            # 인트라데이 5분봉 (tight_flag)
            intraday_bars: pd.DataFrame | None = None
            try:
                idf = yf.download(
                    symbol, period="5d", interval="5m", progress=False, auto_adjust=False
                )
                if idf is not None and not idf.empty:
                    if isinstance(idf.columns, pd.MultiIndex):
                        idf.columns = idf.columns.get_level_values(0)
                    idf.columns = [c.lower() for c in idf.columns]
                    intraday_bars = idf
            except Exception:
                pass

            # RS percentile
            rs_raw_val = rs_pool.get(symbol)
            rs_pct = float(rs_percentile(rs_raw_val, peer_raws)) if rs_raw_val is not None else None

            # 피벗·진입가 사전 계산 (Score C2/C4/D3에 필요)
            pivot_pre, _, _, _, _, _, _ = compute_pick_metadata(
                m, daily_bars, intraday_bars, account_equity
            )

            score, rationale = evaluate_scores(
                m, catalyst, market_ctx,
                is_whitelist=symbol in whitelist_symbols,
                daily_bars=daily_bars,
                intraday_bars=intraday_bars,
                regime_score=regime.score,
                rs_percentile_value=rs_pct,
                benchmark_bars=spy_bars,
                pivot_price=pivot_pre,
                open_price=m.premarket_open or m.premarket_close,
            )

            if score.total < threshold:
                logger.debug("%s below score cut (%.1f < %.1f)", symbol, score.total, threshold)
                continue

            pivot, stop, t1, t2, risk, size, tag = compute_pick_metadata(
                m, daily_bars, intraday_bars, account_equity
            )
            # Regime 조정: 중립모드면 size ×0.7, 방어 ×0.4
            size = int(size * regime.position_size_multiplier())
            picks.append(
                PickRecord(
                    symbol=symbol,
                    rank=0,
                    is_backup=False,
                    metrics=m,
                    gate=gate,
                    score=score,
                    catalyst=catalyst,
                    pivot=pivot,
                    stop=stop,
                    target_1r=t1,
                    target_2r=t2,
                    risk_per_share=risk,
                    position_size=size,
                    strategy_tag=horizon,
                    rationale=rationale,
                )
            )
        except Exception as exc:
            logger.warning("%s pipeline error: %s", symbol, exc)

    # 4) 섹터 중복 압축 → Top 3 + 백업 2
    final = compress_by_sector(picks)

    # 5) DB 저장
    await _save_picks(session, target_date, final, market_ctx)

    return final


async def _save_picks(
    session: AsyncSession,
    target_date: date,
    picks: list[PickRecord],
    market_ctx: dict[str, float],
) -> None:
    """동일 pick_date 기존 레코드 삭제 후 새로 insert (idempotent).

    빈 picks도 항상 commit — 그래야 이전 실행 결과가 정리됨.
    """
    await session.execute(delete(DailyPick).where(DailyPick.pick_date == target_date))
    for p in picks:
        # v3 score_breakdown: 모든 block + penalty 필드 + rationale
        sb_dict = {k: float(v) for k, v in asdict(p.score).items()}
        sb_dict["rationale"] = p.rationale
        # 편의용 block 합계
        sb_dict["block_0"] = float(p.score.block_0)
        sb_dict["block_a"] = float(p.score.block_a)
        sb_dict["block_b"] = float(p.score.block_b)
        sb_dict["block_c"] = float(p.score.block_c)
        sb_dict["block_d"] = float(p.score.block_d)
        sb_dict["penalties_total"] = float(p.score.penalties_total)
        session.add(
            DailyPick(
                pick_date=target_date,
                rank=p.rank,
                symbol=p.symbol,
                is_backup=p.is_backup,
                total_score=Decimal(f"{p.score.total:.2f}"),
                gate_results=asdict(p.gate),
                score_breakdown=sb_dict,
                pivot_price=Decimal(f"{p.pivot:.4f}"),
                stop_price=Decimal(f"{p.stop:.4f}"),
                target_1r=Decimal(f"{p.target_1r:.4f}"),
                target_2r=Decimal(f"{p.target_2r:.4f}"),
                risk_per_share=Decimal(f"{p.risk_per_share:.4f}"),
                position_size=p.position_size,
                strategy_tag=p.strategy_tag,
                catalyst_summary=p.catalyst.summary,
                catalyst_source=p.catalyst.source,
                market_context=market_ctx,
                sector=p.metrics.sector,
            )
        )
    await session.commit()


# ─────────── CLI ───────────


def _serialize_picks(picks: list[PickRecord]) -> list[dict[str, Any]]:
    out = []
    for p in picks:
        sb = {k: round(v, 2) for k, v in asdict(p.score).items()}
        sb["block_0"] = round(p.score.block_0, 2)
        sb["block_a"] = round(p.score.block_a, 2)
        sb["block_b"] = round(p.score.block_b, 2)
        sb["block_c"] = round(p.score.block_c, 2)
        sb["block_d"] = round(p.score.block_d, 2)
        sb["penalties_total"] = round(p.score.penalties_total, 2)
        out.append(
            {
                "rank": p.rank,
                "symbol": p.symbol,
                "is_backup": p.is_backup,
                "score": round(p.score.total, 2),
                "score_breakdown": sb,
                "rationale": p.rationale,
                "gap_pct": round(p.metrics.gap_pct, 2),
                "rvol": round(p.metrics.rvol, 2),
                "catalyst": p.catalyst.summary,
                "catalyst_kind": p.catalyst.primary_kind.value,
                "pivot": round(p.pivot, 2),
                "stop": round(p.stop, 2),
                "target_1r": round(p.target_1r, 2),
                "target_2r": round(p.target_2r, 2),
                "size": p.position_size,
                "strategy_tag": p.strategy_tag,
                "sector": p.metrics.sector,
            }
        )
    return out


async def _run(args: argparse.Namespace) -> None:
    from api.db.session import async_session_factory

    target = (
        date.fromisoformat(args.date) if args.date else date.today()
    )
    # CLI는 명시적 --after-hours-lenient만 True. 미지정 시 None → 시간대 자동 감지.
    lenient: bool | None = True if args.after_hours_lenient else None
    async with async_session_factory() as session:
        picks = await run_daily_picks(
            session,
            target,
            account_equity=args.equity,
            after_hours_lenient=lenient,
        )
    print(json.dumps(_serialize_picks(picks), indent=2, ensure_ascii=False))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Stage 2 Daily Picks (매일 08:55 ET)")
    parser.add_argument("--date", help="ISO date (default: today)")
    parser.add_argument("--equity", type=float, default=DEFAULT_ACCOUNT_EQUITY)
    parser.add_argument(
        "--after-hours-lenient",
        action="store_true",
        help="장 마감 후 demo용 — 프리마켓 거래대금/카탈리스트 게이트 완화",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
