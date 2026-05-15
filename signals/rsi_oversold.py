"""RSI(14) 과매도 시그널 — 평균회귀 진입.

RSI < 30 이면 +1 (반등 후보).
RSI > 70 이면 -1 (과열, 청산 후보).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signals._registry import register_signal

PERIOD = 14
OVERSOLD = 30.0
OVERBOUGHT = 70.0


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing via EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0).astype(float)


@register_signal(
    name="rsi_oversold",
    description="RSI(14) < 30 매수 / > 70 매도",
    category="reversal",
    min_bars=PERIOD + 1,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    rsi = _rsi(bars["close"], PERIOD)
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[rsi < OVERSOLD] = 1
    sig[rsi > OVERBOUGHT] = -1
    return sig
