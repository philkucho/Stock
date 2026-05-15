"""ATR(14) — Average True Range, Wilder smoothing.

스톱·포지션 사이징·변동성 게이트의 표준 입력. Aziz 일평균 ATR ≥ $0.50,
Minervini 권장 stop = entry − 2~3×ATR.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

PERIOD = 14
ATR_GATE_MIN_PCT = 0.015  # 1.5% (단타 RR 확보)
ATR_GATE_MAX_PCT = 0.12   # 12% (변동성 폭주 회피)


def true_range(bars: pd.DataFrame) -> pd.Series:
    """TR_t = max(H−L, |H−prev_close|, |L−prev_close|)."""
    high, low, close = bars["high"], bars["low"], bars["close"]
    prev_close = close.shift(1)
    return pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(bars: pd.DataFrame, period: int = PERIOD) -> pd.Series:
    """Wilder smoothed ATR — alpha = 1/period EMA."""
    tr = true_range(bars)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def atr_pct(bars: pd.DataFrame, period: int = PERIOD) -> pd.Series:
    """ATR as % of close — 변동성을 가격 무관 비율로 측정."""
    return atr(bars, period) / bars["close"]


@register_signal(
    name="atr_in_range",
    description=f"ATR(14) ÷ 가격 ∈ [{int(ATR_GATE_MIN_PCT * 100)}%, {int(ATR_GATE_MAX_PCT * 100)}%] (G9 Gate)",
    category="filter",
    min_bars=PERIOD + 1,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    pct = atr_pct(bars)
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[(pct >= ATR_GATE_MIN_PCT) & (pct <= ATR_GATE_MAX_PCT)] = 1
    return sig
