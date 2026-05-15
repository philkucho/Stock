"""RSI(14) 불리시 모멘텀 시그널.

상승 모멘텀 확인용: RSI > 55 (단순 임계치) AND RSI 어제보다 상승.
oversold 매수가 아닌 "이미 상승 추세에 들어간" 종목 식별 목적.
70 초과는 과열 영역이라 0(중립). 55~70 구간만 +1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signals._registry import register_signal
from signals.rsi_oversold import _rsi  # Wilder smoothing 재사용

PERIOD = 14
LOWER = 55.0
UPPER = 70.0


@register_signal(
    name="rsi_bullish",
    description="RSI(14) 55~70 구간 + 어제 대비 상승 (bullish momentum)",
    category="trend",
    min_bars=PERIOD + 1,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    rsi = _rsi(bars["close"], PERIOD)
    in_band = (rsi > LOWER) & (rsi <= UPPER)
    rising = rsi.diff() > 0
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[in_band & rising] = 1
    return sig
