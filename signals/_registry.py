"""Signal Registry — 단순 불리언 룰들의 카탈로그.

각 시그널은 vectorized: bars DataFrame을 받아 같은 길이의 int Series를 반환.
값: +1 BUY, -1 SELL, 0 NEUTRAL.

여러 시그널을 가중합해서 매수/매도 결정하는 CompositeStrategy에서 소비.
스크리너에서도 재사용 가능 (백테스트와 동일 함수).

사용 예:
    from signals import SIGNAL_REGISTRY
    spec = SIGNAL_REGISTRY["volume_surge"]
    series = spec.evaluate(bars_df)  # 같은 길이의 int Series
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

EvaluateFn = Callable[[pd.DataFrame], pd.Series]


@dataclass(frozen=True)
class SignalSpec:
    name: str
    description: str
    category: str  # volume | trend | reversal | breakout | filter
    min_bars: int  # 평가에 필요한 최소 봉 수 (이 미만 idx는 0 반환)
    evaluate: EvaluateFn

    def evaluate_at(self, bars: pd.DataFrame, idx: int) -> int:
        """단일 시점 평가 — series 계산 후 idx 위치 반환. 빈번한 호출엔 비효율."""
        if idx < self.min_bars:
            return 0
        return int(self.evaluate(bars).iloc[idx])


SIGNAL_REGISTRY: dict[str, SignalSpec] = {}


def register_signal(
    name: str, description: str, category: str, min_bars: int
) -> Callable[[EvaluateFn], EvaluateFn]:
    """시그널 등록 데코레이터. import 시점에 SIGNAL_REGISTRY에 자동 추가."""

    def decorator(fn: EvaluateFn) -> EvaluateFn:
        if name in SIGNAL_REGISTRY:
            raise ValueError(f"Signal '{name}' already registered")
        SIGNAL_REGISTRY[name] = SignalSpec(
            name=name,
            description=description,
            category=category,
            min_bars=min_bars,
            evaluate=fn,
        )
        return fn

    return decorator
