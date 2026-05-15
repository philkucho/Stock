"""이평선 정배열 (5 > 20 > 60) 시그널 — 추세 추종 기본 필터."""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

FAST = 5
MID = 20
SLOW = 60


@register_signal(
    name="ma_alignment",
    description="SMA(5) > SMA(20) > SMA(60) 정배열",
    category="trend",
    min_bars=SLOW,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    close = bars["close"]
    fast = close.rolling(FAST).mean()
    mid = close.rolling(MID).mean()
    slow = close.rolling(SLOW).mean()

    aligned_up = (fast > mid) & (mid > slow)
    aligned_dn = (fast < mid) & (mid < slow)

    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[aligned_up] = 1
    sig[aligned_dn] = -1
    return sig
