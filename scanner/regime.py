"""Market Regime Engine — Block 0 (15점) + mode 판정.

설계서 docs/SELECTION_CRITERIA.md v3 + 플랜 wondrous-coalescing-boole.md 참조.

신호 7개 (총 15점):
  1) SPY > 20EMA                              +2
  2) SPY 20EMA > 50EMA                        +2
  3) QQQ > 20EMA                              +2
  4) IWM 10일 RS vs SPY 양수 (소형주 강세)     +2
  5) VIX < 20 (변동성 안정)                    +2
  6) NYSE A/D Line 5일 상승 (breadth)         +2
  7) 최근 10일 내 Follow-Through Day          +3

Mode 판정:
  - 공격모드 (12-15): A 가중치 정상, position size 정상
  - 중립    (7-11):   A ×0.8, position size ×0.7
  - 방어모드 (0-6):    long 진입 차단, mean-reversion 후보(D1 RSI<30)만 허용. size ×0.4

Phase 1 (yfinance 무료): SPY/QQQ/IWM/^VIX 사용. ^NYAD가 fetch 안 되면 RSP/SPY 비율을 breadth proxy로.
Phase 2 (Polygon): 정확한 NYSE A/D Line + 정밀 FTD 검출.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Any

import pandas as pd

from scanner.benchmarks import get_benchmark_bars

logger = logging.getLogger(__name__)


# ─────────── 점수 가중 ───────────

W_SPY_ABOVE_EMA20 = 2.0
W_SPY_EMA20_ABOVE_EMA50 = 2.0
W_QQQ_ABOVE_EMA20 = 2.0
W_IWM_OUTPERFORM = 2.0
W_VIX_LOW = 2.0
W_AD_LINE_RISING = 2.0
W_FOLLOW_THROUGH_DAY = 3.0

REGIME_MAX = 15.0
MODE_DEFENSIVE_MAX = 6.0   # 0-6
MODE_NEUTRAL_MAX = 11.0    # 7-11
# 12-15 = aggressive

VIX_LOW_THRESHOLD = 20.0
IWM_LOOKBACK = 10
FTD_LOOKBACK = 10
FTD_GAIN_PCT = 1.5  # SPY +1.5% on day
FTD_VOLUME_RATIO = 1.25  # volume vs 50d avg


# ─────────── Mode ───────────


class RegimeMode:
    AGGRESSIVE = "aggressive"
    NEUTRAL = "neutral"
    DEFENSIVE = "defensive"


@dataclass
class RegimeResult:
    score: float = 0.0
    mode: str = RegimeMode.NEUTRAL
    signals: dict[str, bool] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def position_size_multiplier(self) -> float:
        if self.mode == RegimeMode.AGGRESSIVE:
            return 1.0
        if self.mode == RegimeMode.NEUTRAL:
            return 0.7
        return 0.4

    def block_a_weight(self) -> float:
        if self.mode == RegimeMode.AGGRESSIVE:
            return 1.0
        if self.mode == RegimeMode.NEUTRAL:
            return 0.8
        return 0.5

    def long_blocked(self) -> bool:
        return self.mode == RegimeMode.DEFENSIVE


# ─────────── 개별 신호 산출 ───────────


def _check_above_ema(bars: pd.DataFrame, period: int) -> bool | None:
    if bars is None or len(bars) < period:
        return None
    ema = bars["close"].ewm(span=period, adjust=False).mean()
    return bool(bars["close"].iloc[-1] > ema.iloc[-1])


def _check_ema_cross(bars: pd.DataFrame, fast: int, slow: int) -> bool | None:
    if bars is None or len(bars) < slow:
        return None
    fast_ema = bars["close"].ewm(span=fast, adjust=False).mean()
    slow_ema = bars["close"].ewm(span=slow, adjust=False).mean()
    return bool(fast_ema.iloc[-1] > slow_ema.iloc[-1])


def _check_iwm_outperform(iwm: pd.DataFrame, spy: pd.DataFrame, lookback: int) -> bool | None:
    if iwm is None or spy is None:
        return None
    if len(iwm) <= lookback or len(spy) <= lookback:
        return None
    iwm_chg = float(iwm["close"].iloc[-1]) / float(iwm["close"].iloc[-lookback - 1]) - 1
    spy_chg = float(spy["close"].iloc[-1]) / float(spy["close"].iloc[-lookback - 1]) - 1
    return iwm_chg > spy_chg


def _check_vix_low(threshold: float) -> tuple[bool | None, float | None]:
    """^VIX 직접 fetch. 없으면 (None, None)."""
    vix = get_benchmark_bars("^VIX", lookback_days=30)
    if vix is None or vix.empty:
        return None, None
    last_vix = float(vix["close"].iloc[-1])
    return last_vix < threshold, last_vix


def _check_ad_line_rising() -> tuple[bool | None, str | None]:
    """NYSE A/D Line. yfinance ^NYAD 시도, 없으면 RSP/SPY 비율을 breadth proxy."""
    nyad = get_benchmark_bars("^NYAD", lookback_days=30)
    if nyad is not None and len(nyad) >= 6:
        # 5일 단순 추세
        recent5 = nyad["close"].iloc[-6:]
        return bool(recent5.iloc[-1] > recent5.iloc[0]), "^NYAD"
    # Fallback: RSP (equal-weight S&P 500) / SPY ratio 5일 상승
    rsp = get_benchmark_bars("RSP", lookback_days=30)
    spy = get_benchmark_bars("SPY", lookback_days=30)
    if rsp is not None and spy is not None and len(rsp) >= 6 and len(spy) >= 6:
        ratio_now = float(rsp["close"].iloc[-1]) / float(spy["close"].iloc[-1])
        ratio_5d = float(rsp["close"].iloc[-6]) / float(spy["close"].iloc[-6])
        return ratio_now > ratio_5d, "RSP/SPY proxy"
    return None, None


def _check_follow_through_day(spy: pd.DataFrame, lookback: int = FTD_LOOKBACK) -> bool | None:
    """단순화 정의: 최근 N일 내 SPY +1.5% 마감 + 거래량 ≥ 50일 평균 ×1.25.

    원조 IBD FTD는 "rally attempt 시작 후 4-7일에 발생"을 추가 조건으로 두지만
    Phase 1에서는 단순화. Phase 2에서 정확 정의.
    """
    if spy is None or len(spy) < 51:
        return None
    avg_vol_50 = spy["volume"].rolling(50).mean()
    daily_chg = spy["close"].pct_change() * 100
    vol_ratio = spy["volume"] / avg_vol_50
    ftd_mask = (daily_chg >= FTD_GAIN_PCT) & (vol_ratio >= FTD_VOLUME_RATIO)
    return bool(ftd_mask.iloc[-lookback:].any())


# ─────────── 종합 ───────────


def evaluate_regime(target_date: date | None = None) -> RegimeResult:
    """7개 신호 평가 → score 0~15 + mode."""
    spy = get_benchmark_bars("SPY", lookback_days=120)
    qqq = get_benchmark_bars("QQQ", lookback_days=120)
    iwm = get_benchmark_bars("IWM", lookback_days=60)

    signals: dict[str, bool] = {}
    diag: dict[str, Any] = {}
    score = 0.0

    # 1
    s1 = _check_above_ema(spy, 20)
    if s1 is True:
        score += W_SPY_ABOVE_EMA20
    signals["spy_above_20ema"] = bool(s1) if s1 is not None else False

    # 2
    s2 = _check_ema_cross(spy, 20, 50)
    if s2 is True:
        score += W_SPY_EMA20_ABOVE_EMA50
    signals["spy_20ema_above_50ema"] = bool(s2) if s2 is not None else False

    # 3
    s3 = _check_above_ema(qqq, 20)
    if s3 is True:
        score += W_QQQ_ABOVE_EMA20
    signals["qqq_above_20ema"] = bool(s3) if s3 is not None else False

    # 4
    s4 = _check_iwm_outperform(iwm, spy, IWM_LOOKBACK)
    if s4 is True:
        score += W_IWM_OUTPERFORM
    signals["iwm_outperform_spy_10d"] = bool(s4) if s4 is not None else False

    # 5
    s5, vix_val = _check_vix_low(VIX_LOW_THRESHOLD)
    if s5 is True:
        score += W_VIX_LOW
    signals["vix_below_20"] = bool(s5) if s5 is not None else False
    if vix_val is not None:
        diag["vix_value"] = round(vix_val, 2)

    # 6
    s6, ad_source = _check_ad_line_rising()
    if s6 is True:
        score += W_AD_LINE_RISING
    signals["ad_line_5d_rising"] = bool(s6) if s6 is not None else False
    if ad_source is not None:
        diag["ad_source"] = ad_source

    # 7
    s7 = _check_follow_through_day(spy)
    if s7 is True:
        score += W_FOLLOW_THROUGH_DAY
    signals["ftd_within_10d"] = bool(s7) if s7 is not None else False

    # Mode
    if score <= MODE_DEFENSIVE_MAX:
        mode = RegimeMode.DEFENSIVE
    elif score <= MODE_NEUTRAL_MAX:
        mode = RegimeMode.NEUTRAL
    else:
        mode = RegimeMode.AGGRESSIVE

    diag["score_breakdown"] = {
        "spy_above_20ema": W_SPY_ABOVE_EMA20 if s1 else 0.0,
        "spy_ema_cross": W_SPY_EMA20_ABOVE_EMA50 if s2 else 0.0,
        "qqq_above_20ema": W_QQQ_ABOVE_EMA20 if s3 else 0.0,
        "iwm_outperform": W_IWM_OUTPERFORM if s4 else 0.0,
        "vix_low": W_VIX_LOW if s5 else 0.0,
        "ad_line": W_AD_LINE_RISING if s6 else 0.0,
        "ftd": W_FOLLOW_THROUGH_DAY if s7 else 0.0,
    }

    return RegimeResult(score=score, mode=mode, signals=signals, diagnostics=diag)
