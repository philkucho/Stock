"""MACD(12,26,9) 시그널.

표준 파라미터:
- fast EMA(12)
- slow EMA(26)
- signal EMA(9)

매수(+1): histogram > 0 AND 어제는 histogram <= 0 (signal line 상향 돌파).
매도(-1): histogram < 0 AND 어제는 histogram >= 0 (하향 돌파).
그 외 0. — 추세 진입/이탈 모멘트만 캡처.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

FAST = 12
SLOW = 26
SIGNAL = 9


@register_signal(
    name="macd",
    description="MACD(12,26,9) histogram 부호 전환 (bullish/bearish crossover)",
    category="trend",
    min_bars=SLOW + SIGNAL,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    close = bars["close"]
    ema_fast = close.ewm(span=FAST, adjust=False).mean()
    ema_slow = close.ewm(span=SLOW, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=SIGNAL, adjust=False).mean()
    hist = macd_line - signal_line

    prev = hist.shift(1)
    cross_up = (hist > 0) & (prev <= 0)
    cross_dn = (hist < 0) & (prev >= 0)

    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[cross_up] = 1
    sig[cross_dn] = -1
    return sig
