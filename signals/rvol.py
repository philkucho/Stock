"""Relative Volume — 동시간대 평균 거래량 대비 현재 거래량.

Warrior Trading "Gap and Go" 표준의 핵심 모멘텀 게이트. 단순 거래량 급증과 다른 점은
"동시간대 비교"라서 시초 30분/10:00 같은 자연 거래량 패턴을 보정해 준다는 것.

Gate 용도: RVOL ≥ 2× → 시그널 +1
Score 가산: RVOL ≥ 5× 보너스는 스캐너에서 별도 처리 (raw 값 필요).
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

WINDOW = 20
GATE_THRESHOLD = 2.0


def compute_rvol(bars: pd.DataFrame, window: int = WINDOW) -> pd.Series:
    """RVOL raw 값 (current_volume / mean_of_prior_N).

    스캐너의 Score 산식에서 사용. 시그널과 달리 +1/0 변환 안 함.
    """
    avg = bars["volume"].rolling(window).mean().shift(1)
    return bars["volume"] / avg


@register_signal(
    name="rvol",
    description=f"Relative Volume ≥ {GATE_THRESHOLD}× (직전 {WINDOW}봉 평균 대비)",
    category="volume",
    min_bars=WINDOW,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    rvol = compute_rvol(bars, WINDOW)
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[rvol >= GATE_THRESHOLD] = 1
    return sig
