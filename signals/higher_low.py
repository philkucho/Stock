"""직전 저점 갱신 안 함 (상승 추세 유지 확인) 시그널.

최근 N일 최저가가 그 이전 N일 최저가보다 높으면 +1 (higher low).
반대(lower low)면 -1.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

WINDOW = 10


@register_signal(
    name="higher_low",
    description="최근 10일 저점이 그 이전 10일 저점보다 높음 (상승 구조)",
    category="trend",
    min_bars=WINDOW * 2,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    low = bars["low"]
    recent = low.rolling(WINDOW).min()
    prior = low.rolling(WINDOW).min().shift(WINDOW)
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[recent > prior] = 1
    sig[recent < prior] = -1
    return sig
