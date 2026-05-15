"""20일 고점 돌파 시그널 — CIS식 추세 진입."""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

WINDOW = 20


@register_signal(
    name="breakout_20d",
    description="종가가 직전 20일 최고가를 상향 돌파",
    category="breakout",
    min_bars=WINDOW,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    prior_high = bars["high"].rolling(WINDOW).max().shift(1)
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[bars["close"] > prior_high] = 1
    return sig
