"""52주 고점 근접 — Minervini Trend Template의 핵심 조건 중 하나.

이전 252거래일 (≈ 1년) 최고가의 X% 이내. 강세 종목 후보 풀의 1차 필터로 사용.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

WINDOW = 252
PROXIMITY_PCT = 0.03  # 3% 이내


def distance_to_52w_high(bars: pd.DataFrame, window: int = WINDOW) -> pd.Series:
    """현재 close가 52주 고점에서 얼마나 떨어졌는지 (양수 = 아래)."""
    high_52w = bars["high"].rolling(window).max()
    return (high_52w - bars["close"]) / high_52w


@register_signal(
    name="near_52w_high",
    description=f"종가가 직전 {WINDOW}일 고점의 {int(PROXIMITY_PCT * 100)}% 이내",
    category="trend",
    min_bars=WINDOW,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    dist = distance_to_52w_high(bars, WINDOW)
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[dist <= PROXIMITY_PCT] = 1
    return sig
