"""RSI Structure — D1 Risk score (v3 변경, 5점).

v2 단순 절대값 페널티 (RSI > 70 = -3) 폐기 사유:
  강한 모멘텀 leader (NVDA, SMCI, PLTR, ARM, VRT)는 RSI 75-90에 몇 주~몇 달 머묾.
  단순 임계값 페널티는 super leader를 잘못 배제.

v3는 *level* 대신 *behavior* 기반:
  GOOD (5점): RSI 50~75 + 직전 5봉 higher low + 가격↑일 때 RSI도 ↑ (no divergence)
  NEUTRAL (2점): RSI 75~90 + higher highs (super leader 영역, 추세 유지)
  BAD (0점): RSI lower high while price higher (bearish divergence) OR RSI >85 + 거래량 climax

P5 Penalty 트리거: RSI 85+ AND 거래량 climax (직전 5봉 평균 대비 200%↑)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from signals.rsi_oversold import _rsi

GOOD_RSI_MIN = 50.0
GOOD_RSI_MAX = 75.0
SUPER_LEADER_RSI_MAX = 90.0
DIVERGENCE_LOOKBACK = 5
CLIMAX_VOLUME_RATIO = 2.0  # 직전 5봉 평균 대비 climax


@dataclass
class RsiStructureResult:
    score: float = 0.0           # 0~5 (D1 점수)
    grade: str = "neutral"        # "good" | "neutral" | "bad"
    rsi_value: float = 50.0
    has_higher_low: bool = False
    has_bearish_divergence: bool = False
    is_climax: bool = False       # P5 페널티 트리거
    notes: str = ""


def detect_rsi_structure(bars: pd.DataFrame, period: int = 14) -> RsiStructureResult:
    """일봉 기준 RSI 구조 평가. 마지막 봉 기준."""
    r = RsiStructureResult()
    if bars is None or len(bars) < period + DIVERGENCE_LOOKBACK + 1:
        return r

    rsi = _rsi(bars["close"], period)
    rsi_now = float(rsi.iloc[-1])
    r.rsi_value = rsi_now

    # 직전 5봉 RSI higher low 패턴 확인
    recent_rsi = rsi.iloc[-DIVERGENCE_LOOKBACK:]
    recent_lows = recent_rsi.rolling(2).min()
    # 단순화: RSI 최근 5봉의 최소값이 직전 5봉 최소값보다 높으면 higher low
    if len(rsi) >= 2 * DIVERGENCE_LOOKBACK:
        prior_low_window = rsi.iloc[-2 * DIVERGENCE_LOOKBACK:-DIVERGENCE_LOOKBACK]
        if recent_rsi.min() > prior_low_window.min():
            r.has_higher_low = True

    # Bearish divergence: 가격 신고가 + RSI 신고가 미달
    recent_close = bars["close"].iloc[-DIVERGENCE_LOOKBACK:]
    if len(bars) >= 2 * DIVERGENCE_LOOKBACK:
        prior_close = bars["close"].iloc[-2 * DIVERGENCE_LOOKBACK:-DIVERGENCE_LOOKBACK]
        prior_rsi = rsi.iloc[-2 * DIVERGENCE_LOOKBACK:-DIVERGENCE_LOOKBACK]
        price_higher = recent_close.max() > prior_close.max()
        rsi_lower = recent_rsi.max() < prior_rsi.max()
        if price_higher and rsi_lower:
            r.has_bearish_divergence = True

    # Climax 판정 (P5 트리거): RSI 85+ + 직전 5봉 평균 대비 거래량 200%+
    if rsi_now >= 85.0 and len(bars) >= 6:
        avg_vol_5 = float(bars["volume"].iloc[-6:-1].mean())
        last_vol = float(bars["volume"].iloc[-1])
        if avg_vol_5 > 0 and last_vol >= avg_vol_5 * CLIMAX_VOLUME_RATIO:
            r.is_climax = True

    # Grade 판정
    if r.has_bearish_divergence or r.is_climax:
        r.grade = "bad"
        r.score = 0.0
        if r.has_bearish_divergence:
            r.notes = "가격 신고가 vs RSI 미달 (베어리시 다이버전스)"
        elif r.is_climax:
            r.notes = "RSI 85+ AND 거래량 climax — 단기 정점 위험"
    elif GOOD_RSI_MIN <= rsi_now <= GOOD_RSI_MAX and r.has_higher_low:
        r.grade = "good"
        r.score = 5.0
        r.notes = f"RSI {rsi_now:.1f} 강세 + higher low 유지"
    elif GOOD_RSI_MAX < rsi_now <= SUPER_LEADER_RSI_MAX:
        # super leader 영역 — 임계값 페널티 안 줌
        r.grade = "neutral"
        r.score = 2.0
        r.notes = f"RSI {rsi_now:.1f} super leader 영역 (모멘텀 유지)"
    elif GOOD_RSI_MIN <= rsi_now <= GOOD_RSI_MAX:
        r.grade = "neutral"
        r.score = 3.0
        r.notes = f"RSI {rsi_now:.1f} 강세 영역"
    elif rsi_now < 30:
        r.grade = "neutral"
        r.score = 3.0
        r.notes = f"RSI {rsi_now:.1f} 과매도 (반등 후보)"
    else:
        r.grade = "neutral"
        r.score = 1.0
        r.notes = f"RSI {rsi_now:.1f} 약세/중립"

    return r
