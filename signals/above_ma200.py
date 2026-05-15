"""장기 추세 필터 — 종가가 SMA(200) 위인지."""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

PERIOD = 200


@register_signal(
    name="above_ma200",
    description="종가 > SMA(200): 장기 상승추세 필터",
    category="filter",
    min_bars=PERIOD,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    ma = bars["close"].rolling(PERIOD).mean()
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[bars["close"] > ma] = 1
    sig[bars["close"] < ma] = -1
    return sig
