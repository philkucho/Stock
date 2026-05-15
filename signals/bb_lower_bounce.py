"""Bollinger Band 하단 반등 시그널.

직전 봉의 종가가 하단 밴드 이하 + 현재 봉이 하단 밴드 위로 복귀 = 평균회귀 매수.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

PERIOD = 20
NUM_STD = 2.0


@register_signal(
    name="bb_lower_bounce",
    description="Bollinger 하단 터치 후 반등 (PERIOD=20, std=2)",
    category="reversal",
    min_bars=PERIOD + 1,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    close = bars["close"]
    mid = close.rolling(PERIOD).mean()
    std = close.rolling(PERIOD).std(ddof=0)
    lower = mid - NUM_STD * std

    prev_below = (close.shift(1) <= lower.shift(1))
    now_above = close > lower

    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[prev_below & now_above] = 1
    return sig
