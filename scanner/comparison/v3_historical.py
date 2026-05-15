"""v3 Daily Picks — historical 재계산.

운영 stage2_daily_picks는 yfinance fast_info(현재 시점)에 의존하므로 과거 날짜 평가 불가.
이 모듈은 일봉 데이터만으로 v3 알고리즘을 재구성 — backfill 비교용.

차이점:
  - 프리마켓 데이터 불가 → m.rvol=0, premarket_dollar_vol=0
  - after_hours_lenient=True 강제 (G2/G12 우회)
  - gap_pct = (target_date open / prev close − 1) × 100, daily bars로 계산
  - tight_flag (5분봉 패턴) 비활성 — 일봉 기반 패턴(20일 신고가, 52주 고점)만 평가
  - sector·float은 현재 yfinance .info 사용 (대부분 변동 적음)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import UniverseMember
from backtests.data_cache import get_bars
from scanner.benchmarks import get_benchmark_bars
from scanner.catalysts.types import CatalystKind, CatalystScore
from scanner.comparison.adapters import PickCandidate
from scanner.regime import evaluate_regime
from scanner.stage2_daily_picks import (
    SCORE_THRESHOLD,
    SCORE_THRESHOLD_LENIENT,
    CandidateMetrics,
    compress_by_sector,
    evaluate_gates,
    evaluate_scores,
)
from signals.atr import atr_pct as atr_pct_series
from signals.relative_strength import rs_ibd_raw, rs_percentile
from signals.stage2_trend_template import trend_template_pass

logger = logging.getLogger(__name__)


def _slice_to_date(bars: pd.DataFrame, target_date: date) -> pd.DataFrame:
    """target_date 까지의 bars (target_date 포함)."""
    if bars is None or bars.empty:
        return pd.DataFrame()
    target_ts = pd.Timestamp(target_date, tz="UTC")
    return bars[bars.index <= target_ts]


def _historical_metrics(
    symbol: str,
    target_date: date,
    daily_bars: pd.DataFrame,
    sector_cache: dict[str, str | None],
    float_cache: dict[str, float | None],
) -> CandidateMetrics | None:
    """과거 날짜 기준 metric 구성. 프리마켓 항목은 None/0."""
    if daily_bars is None or len(daily_bars) < 2:
        return None
    last = daily_bars.iloc[-1]
    prev = daily_bars.iloc[-2]

    open_t = float(last["open"])
    prev_close = float(prev["close"])
    if prev_close <= 0:
        return None

    gap_pct = (open_t - prev_close) / prev_close * 100.0

    m = CandidateMetrics(symbol=symbol)
    m.prev_close = prev_close
    m.premarket_close = open_t  # 시초가를 "프리마켓 마감(=정규장 open)"으로 사용
    m.premarket_open = open_t
    m.premarket_high = float(last["high"])
    m.premarket_low = float(last["low"])
    m.gap_pct = gap_pct
    m.rvol = 0.0  # 프리마켓 RVOL 불가
    m.premarket_dollar_vol = 0.0
    m.bid = None
    m.ask = None
    m.spread_pct = None
    m.yesterday_high = float(prev["high"])
    m.sector = sector_cache.get(symbol)
    m.float_shares = float_cache.get(symbol)
    m.market_cap = None
    return m


async def run_v3_for_date(
    session: AsyncSession,
    target_date: date,
    top: int = 5,
) -> list[PickCandidate]:
    """과거 날짜 기준 v3 picks 재계산. system_pick_logs에 저장은 logger가 담당.

    수익 비교가 목적이라 daily_picks 테이블에는 저장하지 않음.
    """
    # 1) Universe 로드 — 과거 날짜에도 현재 universe 멤버를 사용 (간단화)
    stmt = select(UniverseMember).where(
        UniverseMember.enabled == True,  # noqa: E712
        UniverseMember.source != "blacklist",
    )
    result = await session.execute(stmt)
    members = list(result.scalars().all())
    if not members:
        return []
    whitelist_symbols = {m.symbol for m in members if m.source == "score5_whitelist"}
    candidates = sorted({m.symbol for m in members})

    # 2) 벤치마크 bars (target_date까지)
    spy_full = get_benchmark_bars("SPY", lookback_days=400)
    spy_bars = _slice_to_date(spy_full, target_date) if spy_full is not None else None

    # Regime — 현재 시점 기반 (historical regime은 별도 구현 필요. 일단 현재 사용)
    # NOTE: 정확한 historical regime을 위해서는 evaluate_regime을 target_date 기반으로 수정해야 함
    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.debug("v3 historical %s: defensive regime, picks blocked", target_date)
        return []

    # 3) 종목별 일봉 + sector/float 캐시
    end_iso = target_date.isoformat()
    start_iso = (target_date - timedelta(days=400)).isoformat()
    daily_cache: dict[str, pd.DataFrame] = {}
    sector_cache: dict[str, str | None] = {}
    float_cache: dict[str, float | None] = {}

    # 비교용 RS percentile pool
    rs_pool: dict[str, float | None] = {}
    for sym in candidates + ["SPY", "QQQ", "IWM"]:
        try:
            full = get_bars(sym, start_iso, end_iso, "1d")
            sliced = _slice_to_date(full, target_date)
            if len(sliced) >= 252:
                raw = float(rs_ibd_raw(sliced).iloc[-1])
                rs_pool[sym] = raw if pd.notna(raw) else None
            else:
                rs_pool[sym] = None
            daily_cache[sym] = sliced
        except Exception:
            rs_pool[sym] = None
            daily_cache[sym] = pd.DataFrame()
    peer_raws = [v for v in rs_pool.values() if v is not None]

    # sector / float — 현재 yfinance .info (간단화)
    import yfinance as yf
    for sym in candidates:
        try:
            info = yf.Ticker(sym).info
            sector_cache[sym] = info.get("sector")
            float_cache[sym] = float(info.get("floatShares") or 0) or None
        except Exception:
            sector_cache[sym] = None
            float_cache[sym] = None

    # 4) 평가
    pick_records: list[tuple[float, PickCandidate]] = []
    threshold = SCORE_THRESHOLD_LENIENT  # historical은 lenient로

    # Empty catalyst (historical 정확한 catalyst data 부족)
    empty_catalyst = CatalystScore(
        score=0, primary_kind=CatalystKind.NONE, summary="", source=""
    )
    market_ctx = {}  # 섹터 ETF 갭은 historical에서 산출 어려움 → 빈 dict

    for symbol in candidates:
        try:
            daily_bars = daily_cache.get(symbol)
            if daily_bars is None or len(daily_bars) < 252:
                continue
            m = _historical_metrics(
                symbol, target_date, daily_bars, sector_cache, float_cache
            )
            if m is None:
                continue

            # 일봉 거래량 비율
            avg_vol = float(daily_bars["volume"].iloc[-21:-1].mean())
            last_vol = float(daily_bars["volume"].iloc[-1])
            daily_vol_ratio = last_vol / avg_vol if avg_vol > 0 else None

            # Stage 2 사전 계산
            tt_pass = bool(trend_template_pass(daily_bars).iloc[-1])

            # Gate 평가 (after_hours_lenient=True)
            gate = evaluate_gates(
                m, empty_catalyst, market_ctx, target_date,
                after_hours_lenient=True,
                daily_volume_ratio=daily_vol_ratio,
                daily_bars=daily_bars,
                horizon="swing",
                regime_score=regime.score,
                trend_template_passed=tt_pass,
            )
            if not gate.all_passed():
                continue

            # RS percentile
            rs_raw = rs_pool.get(symbol)
            rs_pct = float(rs_percentile(rs_raw, peer_raws)) if rs_raw is not None else None

            # 피벗(=시초가) 사전값
            pivot_pre = m.yesterday_high or m.premarket_close

            score, rationale = evaluate_scores(
                m, empty_catalyst, market_ctx,
                is_whitelist=symbol in whitelist_symbols,
                daily_bars=daily_bars,
                intraday_bars=None,  # 과거 5분봉 없음
                regime_score=regime.score,
                rs_percentile_value=rs_pct,
                benchmark_bars=spy_bars,
                pivot_price=pivot_pre,
                open_price=m.premarket_open,
            )
            if score.total < threshold:
                continue

            pc = PickCandidate(
                system_id="v3",
                rank=0,  # 정렬 후 부여
                symbol=symbol,
                score=float(score.total),
                score_meta={
                    "block_0": float(score.block_0),
                    "block_a": float(score.block_a),
                    "block_b": float(score.block_b),
                    "block_c": float(score.block_c),
                    "block_d": float(score.block_d),
                    "penalties_total": float(score.penalties_total),
                    "rationale": rationale,
                    "historical": True,
                },
                sector=m.sector,
                strategy_tag="swing",
            )
            pick_records.append((score.total, pc))
        except Exception as exc:
            logger.debug("v3 historical %s %s error: %s", target_date, symbol, exc)

    # 5) Top N + 섹터 압축은 단순화 — 점수순 top N
    pick_records.sort(key=lambda x: x[0], reverse=True)
    top_picks = pick_records[:top]
    for i, (_, pc) in enumerate(top_picks, start=1):
        pc.rank = i

    return [pc for _, pc in top_picks]
