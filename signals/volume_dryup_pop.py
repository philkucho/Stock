"""거래량 마름 후 폭증 패턴 (BNF식).

직전 5일 거래량이 20일 평균의 70% 이하로 마른 상태 + 현재 봉 거래량이 200% 폭증.
세력의 매집 후 분출 패턴 가설.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

DRY_WINDOW = 5
AVG_WINDOW = 20
DRY_RATIO = 0.7
POP_RATIO = 2.0


@register_signal(
    name="volume_dryup_pop",
    description="5일 거래량 마름(20일평균 70% 이하) 후 1봉 폭증(200%)",
    category="volume",
    min_bars=AVG_WINDOW + DRY_WINDOW,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    vol = bars["volume"]
    avg = vol.rolling(AVG_WINDOW).mean().shift(1)

    # 직전 5일 평균 거래량이 20일 평균의 DRY_RATIO 이하
    recent_avg = vol.rolling(DRY_WINDOW).mean().shift(1)
    dryup = recent_avg <= avg * DRY_RATIO

    # 현재 봉 거래량이 20일 평균의 POP_RATIO 초과
    pop = vol > avg * POP_RATIO

    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[dryup & pop] = 1
    return sig
