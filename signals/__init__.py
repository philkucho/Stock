"""Signal Library — composite voting strategy의 빌딩블록.

각 시그널은 vectorized: bars DataFrame → 같은 길이의 int Series (-1 / 0 / +1).
import 시점에 모든 시그널 모듈이 SIGNAL_REGISTRY에 자동 등록됨.

사용 예:
    from signals import SIGNAL_REGISTRY
    spec = SIGNAL_REGISTRY["volume_surge"]
    series = spec.evaluate(bars_df)
"""

from __future__ import annotations

from signals._registry import SIGNAL_REGISTRY, SignalSpec, register_signal

# 아래 import는 부수효과 (각 모듈이 register_signal 데코레이터로 SIGNAL_REGISTRY 등록).
# 순서 의존성 없음. import 실패 시 해당 시그널만 누락되도록 try/except 안 씀 — 빨리 실패.
from signals import (  # noqa: F401  (side-effect imports)
    above_ma200,
    atr,
    bb_lower_bounce,
    bnf_ma25_divergence,
    breakout_20d,
    golden_cross,
    higher_low,
    ma_alignment,
    macd,
    near_52w_high,
    relative_strength,
    rsi_bullish,
    rsi_oversold,
    rvol,
    stage2_trend_template,
    support_bounce,
    tight_base,
    tight_flag_setup,
    volume_dryup_pop,
    volume_surge,
    volume_trend,
    vwap_reclaim,
)

__all__ = ["SIGNAL_REGISTRY", "SignalSpec", "register_signal"]
