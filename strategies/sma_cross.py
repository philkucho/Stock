"""SMA Crossover 전략.

단순 이동평균선 골든/데드 크로스 기반 진입/청산. NautilusTrader Strategy 베이스.
백테스트 / Paper / 라이브 동일 코드로 동작 (환경만 교체).

레퍼런스: nautilus_trader/examples/strategies/ema_cross.py
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy


class SmaCrossConfig(StrategyConfig, frozen=True):
    """SmaCross 설정.

    fast_period < slow_period 강제. trade_size는 기본 통화 기준 수량 (주식이면 주수).
    """

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_period: PositiveInt = 10
    slow_period: PositiveInt = 30
    close_positions_on_stop: bool = True


class SmaCross(Strategy):
    """단기 SMA가 장기 SMA를 상향 돌파하면 매수, 하향 돌파하면 매도.

    Long-only 흐름: 매수 신호에서 진입, 매도 신호에서 청산 (숏 포지션 없음).
    EMA 예제와 달리 SMA 사용. 골든크로스 한 번 발생하면 한 번만 진입함
    (`portfolio.is_flat` 체크로 중복 진입 방지).
    """

    def __init__(self, config: SmaCrossConfig) -> None:
        PyCondition.is_true(
            config.fast_period < config.slow_period,
            f"fast_period ({config.fast_period}) must be < slow_period ({config.slow_period})",
        )
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.fast_sma = SimpleMovingAverage(config.fast_period)
        self.slow_sma = SimpleMovingAverage(config.slow_period)

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument: {self.config.instrument_id}")
            self.stop()
            return

        self.register_indicator_for_bars(self.config.bar_type, self.fast_sma)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_sma)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self.indicators_initialized():
            return
        if bar.is_single_price():
            return

        if self.fast_sma.value > self.slow_sma.value:
            if self.portfolio.is_flat(self.config.instrument_id):
                self._submit(OrderSide.BUY)
        elif self.fast_sma.value < self.slow_sma.value:
            if self.portfolio.is_net_long(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)

    def _submit(self, side: OrderSide) -> None:
        assert self.instrument is not None
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        self.log.info(
            f"{side.name} {self.config.instrument_id} qty={self.config.trade_size}",
            color=LogColor.GREEN,
        )

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_reset(self) -> None:
        self.fast_sma.reset()
        self.slow_sma.reset()
