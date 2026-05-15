"""Strategy 모듈.

CompositeStrategy 가 메인 — 여러 시그널의 가중합으로 매매 결정.
SmaCross 는 레거시 데모/레퍼런스로 보존 (단일 시그널 진입의 단순 케이스).
"""

from __future__ import annotations

from nautilus_trader.trading.strategy import Strategy

from strategies.composite import CompositeStrategy, CompositeStrategyConfig
from strategies.presets import PRESETS, get_preset, list_presets
from strategies.sma_cross import SmaCross, SmaCrossConfig

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "composite": CompositeStrategy,
    "sma_cross": SmaCross,
}

__all__ = [
    "CompositeStrategy",
    "CompositeStrategyConfig",
    "PRESETS",
    "STRATEGY_REGISTRY",
    "SmaCross",
    "SmaCrossConfig",
    "get_preset",
    "list_presets",
]
