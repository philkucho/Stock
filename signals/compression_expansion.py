"""Compression / Expansion — C5 Setup score (v3 신규, 6점).

Stockbee NR + Minervini VCP 본질의 알고리즘화:
  - 변동성이 점점 줄어드는(compression) → 폭발 직전 신호
  - 폭발 직후(expansion) → 진입 시점 확인

산식:
  +3 compression: 직전 5일 평균 ATR% ≤ 직전 30일 평균 ATR%의 70%
  +3 expansion (start): 오늘 ATR% > 직전 5일 평균 ATR%의 150%
  → 둘 다 충족 = 6점 (golden setup), 한쪽만 = 3점, 둘 다 fail = 0점
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from signals.atr import atr_pct as atr_pct_series

COMPRESSION_RATIO = 0.7      # 직전 5d/30d 평균이 70% 이하면 compression
EXPANSION_RATIO = 1.5        # 오늘이 5d 평균의 150% 초과면 expansion 시작
COMPRESSION_LOOKBACK = 5
COMPRESSION_BASELINE = 30


@dataclass
class CompressionExpansionResult:
    score: float = 0.0  # 0~6
    is_compression: bool = False
    is_expansion: bool = False
    compression_ratio: float = 1.0
    expansion_ratio: float = 1.0


def detect_compression_expansion(
    bars: pd.DataFrame,
    atr_period: int = 14,
) -> CompressionExpansionResult:
    """일봉 입력. 마지막 봉 기준 compression/expansion 평가."""
    r = CompressionExpansionResult()
    if bars is None or len(bars) < COMPRESSION_BASELINE + atr_period:
        return r

    pct = atr_pct_series(bars, atr_period).dropna()
    if len(pct) < COMPRESSION_BASELINE:
        return r

    recent5 = pct.iloc[-COMPRESSION_LOOKBACK:].mean()
    baseline30 = pct.iloc[-(COMPRESSION_BASELINE + 1):-1].mean()  # 마지막 봉 제외
    today_atr = pct.iloc[-1]

    if baseline30 > 0:
        r.compression_ratio = float(recent5 / baseline30)
    if recent5 > 0:
        r.expansion_ratio = float(today_atr / recent5)

    if r.compression_ratio <= COMPRESSION_RATIO:
        r.is_compression = True
        r.score += 3.0

    if r.expansion_ratio >= EXPANSION_RATIO:
        r.is_expansion = True
        r.score += 3.0

    r.score = max(0.0, min(6.0, r.score))
    return r
