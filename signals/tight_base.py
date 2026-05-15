"""타이트 베이스 — Minervini VCP의 일봉 단순화 형태.

직전 N일의 (high - low)/close 변동성이 점진 축소 + 마지막 N/2일이 좁은 박스.
1분/5분봉 패턴은 별도 `tight_flag_setup` 모듈을 사용.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

WINDOW = 30
TIGHT_RATIO = 0.5  # 마지막 N/2일 평균 range가 직전 N/2일의 50% 이하면 "타이트"


def base_tightness_score(bars: pd.DataFrame, window: int = WINDOW) -> pd.Series:
    """0~1 스코어 — 0 = 매우 산만, 1 = 매우 타이트.

    `tight_ratio = recent_avg_range / earlier_avg_range`
    score = clamp(1 - tight_ratio, 0, 1)
    """
    daily_range = (bars["high"] - bars["low"]) / bars["close"]
    half = window // 2
    earlier = daily_range.rolling(half).mean().shift(half)
    recent = daily_range.rolling(half).mean()
    ratio = recent / earlier
    score = (1 - ratio).clip(lower=0, upper=1)
    return score


@register_signal(
    name="tight_base",
    description=f"직전 {WINDOW}일 베이스가 타이트 (최근 {WINDOW // 2}일 range가 이전 {WINDOW // 2}일의 {int(TIGHT_RATIO * 100)}% 이하)",
    category="breakout",
    min_bars=WINDOW,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    daily_range = (bars["high"] - bars["low"]) / bars["close"]
    half = WINDOW // 2
    earlier = daily_range.rolling(half).mean().shift(half)
    recent = daily_range.rolling(half).mean()
    ratio = recent / earlier
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[ratio <= TIGHT_RATIO] = 1
    return sig
