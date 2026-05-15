"""골든크로스/데드크로스 시그널 — SMA(5)가 SMA(20)을 상향/하향 돌파한 봉."""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

FAST = 5
SLOW = 20


@register_signal(
    name="golden_cross",
    description="SMA(5)가 SMA(20)을 상향 돌파한 봉만 +1, 하향 돌파만 -1",
    category="trend",
    min_bars=SLOW + 1,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    close = bars["close"]
    fast = close.rolling(FAST).mean()
    slow = close.rolling(SLOW).mean()

    above = fast > slow
    above_prev = above.shift(1, fill_value=False)

    cross_up = above & ~above_prev
    cross_dn = ~above & above_prev

    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[cross_up] = 1
    sig[cross_dn] = -1
    return sig
