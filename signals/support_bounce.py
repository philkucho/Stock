"""지지선 반등 시그널.

직전 5일 최저가 ±1% 범위에 저점이 닿고, 종가가 시가보다 높음 (반등 양봉).
단타 진입 패턴 (BNF식 단기 반전).
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

WINDOW = 5
TOLERANCE = 0.01


@register_signal(
    name="support_bounce",
    description="직전 5일 저점 ±1% 터치 후 양봉 반등",
    category="reversal",
    min_bars=WINDOW + 1,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    prior_low = bars["low"].rolling(WINDOW).min().shift(1)
    touched = bars["low"] <= prior_low * (1.0 + TOLERANCE)
    bullish = bars["close"] > bars["open"]
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[touched & bullish] = 1
    return sig
