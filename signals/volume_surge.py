"""거래량 급증 시그널 (BNF 핵심)."""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

WINDOW = 20
THRESHOLD = 2.0


@register_signal(
    name="volume_surge",
    description="거래량 20일 평균 대비 200% 초과",
    category="volume",
    min_bars=WINDOW,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    avg = bars["volume"].rolling(WINDOW).mean().shift(1)
    ratio = bars["volume"] / avg
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[ratio > THRESHOLD] = 1
    return sig
