"""SmaCrossPlus — SMA 크로스 + RSI 필터 + ATR 기반 손절/익절.

기본 SmaCross 대비 추가 사항:
1. **RSI 필터**: 매수 신호 발생 시 RSI > rsi_overbought면 진입 회피 (과매수 회피)
2. **ATR 기반 손절/익절**: 진입 시 stop = entry - atr*K, target = entry + atr*M
3. **(선택) Trailing stop**: 신고가 갱신 시 stop을 atr*K 거리에 따라가게 함
4. **데드크로스 청산은 유지**: SMA 신호 반대 방향이면 stop/target 무관하게 청산

여전히 long-only. trade_size는 고정 (risk-based 사이징은 추후 확장).
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import PositiveFloat, PositiveInt, StrategyConfig
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import (
    AverageTrueRange,
    RelativeStrengthIndex,
    SimpleMovingAverage,
)
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy


class SmaCrossPlusConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal

    # SMA
    fast_period: PositiveInt = 10
    slow_period: PositiveInt = 30

    # RSI 필터
    use_rsi_filter: bool = True
    rsi_period: PositiveInt = 14
    rsi_overbought: PositiveFloat = 70.0  # 매수 시 RSI > 이 값이면 진입 회피

    # ATR 기반 손절/익절
    use_atr_stops: bool = True
    atr_period: PositiveInt = 14
    stop_atr_mult: PositiveFloat = 2.0  # stop = entry - atr * stop_atr_mult
    target_atr_mult: PositiveFloat = 4.0  # target = entry + atr * target_atr_mult
    use_trailing_stop: bool = False  # True면 신고가 갱신 시 stop을 따라감

    close_positions_on_stop: bool = True


class SmaCrossPlus(Strategy):
    """SMA 골든크로스 매수 + 손절/익절/필터 적용. Long-only."""

    def __init__(self, config: SmaCrossPlusConfig) -> None:
        PyCondition.is_true(
            config.fast_period < config.slow_period,
            f"fast_period ({config.fast_period}) must be < slow_period ({config.slow_period})",
        )
        super().__init__(config)

        self.instrument: Instrument | None = None

        self.fast_sma = SimpleMovingAverage(period=config.fast_period)
        self.slow_sma = SimpleMovingAverage(period=config.slow_period)
        self.rsi = (
            RelativeStrengthIndex(period=config.rsi_period) if config.use_rsi_filter else None
        )
        self.atr = AverageTrueRange(period=config.atr_period) if config.use_atr_stops else None

        # 진입 후 추적 상태 (포지션 보유 시에만 유효)
        self._entry_price: float | None = None
        self._stop_price: float | None = None
        self._target_price: float | None = None
        self._highest_since_entry: float | None = None  # trailing stop용

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument: {self.config.instrument_id}")
            self.stop()
            return

        self.register_indicator_for_bars(self.config.bar_type, self.fast_sma)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_sma)
        if self.rsi is not None:
            self.register_indicator_for_bars(self.config.bar_type, self.rsi)
        if self.atr is not None:
            self.register_indicator_for_bars(self.config.bar_type, self.atr)

        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self.indicators_initialized():
            return
        if bar.is_single_price():
            return

        is_long = self.portfolio.is_net_long(self.config.instrument_id)

        # 1) 보유 중: stop/target 체크가 SMA 신호보다 우선
        if is_long and self._entry_price is not None:
            if self._check_exit_levels(bar):
                return

        # 2) SMA 신호 평가
        fast = self.fast_sma.value
        slow = self.slow_sma.value

        if fast > slow:
            # Long entry signal
            if self.portfolio.is_flat(self.config.instrument_id):
                if self._rsi_blocks_entry():
                    self.log.info(
                        f"Entry blocked by RSI={self.rsi.value:.1f} > {self.config.rsi_overbought}",
                        color=LogColor.YELLOW,
                    )
                    return
                self._enter_long(bar)
        elif fast < slow:
            # Dead cross — exit any long position
            if self.portfolio.is_net_long(self.config.instrument_id):
                self._exit("dead_cross")

    # ── helpers ──────────────────────────────────────────────────────────

    def _rsi_blocks_entry(self) -> bool:
        if self.rsi is None:
            return False
        return self.rsi.value > float(self.config.rsi_overbought)

    def _check_exit_levels(self, bar: Bar) -> bool:
        """Trailing stop 갱신 + stop/target 도달 체크. 청산 발생 시 True 반환."""
        if not self.config.use_atr_stops:
            return False

        high = bar.high.as_double()
        low = bar.low.as_double()

        # Trailing stop 갱신 (신고가 갱신 시)
        if self.config.use_trailing_stop and self.atr is not None:
            if self._highest_since_entry is None or high > self._highest_since_entry:
                self._highest_since_entry = high
                trailed = high - self.atr.value * float(self.config.stop_atr_mult)
                if self._stop_price is None or trailed > self._stop_price:
                    self._stop_price = trailed

        # Stop hit
        if self._stop_price is not None and low <= self._stop_price:
            self.log.info(
                f"STOP hit @ {self._stop_price:.2f} (low={low:.2f})",
                color=LogColor.RED,
            )
            self._exit("stop")
            return True

        # Target hit
        if self._target_price is not None and high >= self._target_price:
            self.log.info(
                f"TARGET hit @ {self._target_price:.2f} (high={high:.2f})",
                color=LogColor.GREEN,
            )
            self._exit("target")
            return True

        return False

    def _enter_long(self, bar: Bar) -> None:
        assert self.instrument is not None
        entry = bar.close.as_double()
        self._entry_price = entry
        self._highest_since_entry = entry

        if self.config.use_atr_stops and self.atr is not None and self.atr.value > 0:
            atr_v = self.atr.value
            self._stop_price = entry - atr_v * float(self.config.stop_atr_mult)
            self._target_price = entry + atr_v * float(self.config.target_atr_mult)
        else:
            self._stop_price = None
            self._target_price = None

        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(self.config.trade_size),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        self.log.info(
            f"BUY {self.config.instrument_id} entry={entry:.2f} "
            f"stop={self._stop_price} target={self._target_price}",
            color=LogColor.GREEN,
        )

    def _exit(self, reason: str) -> None:
        self.close_all_positions(self.config.instrument_id)
        self.log.info(f"EXIT ({reason})", color=LogColor.MAGENTA)
        self._entry_price = None
        self._stop_price = None
        self._target_price = None
        self._highest_since_entry = None

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)
        self._entry_price = None
        self._stop_price = None
        self._target_price = None
        self._highest_since_entry = None

    def on_reset(self) -> None:
        self.fast_sma.reset()
        self.slow_sma.reset()
        if self.rsi is not None:
            self.rsi.reset()
        if self.atr is not None:
            self.atr.reset()
        self._entry_price = None
        self._stop_price = None
        self._target_price = None
        self._highest_since_entry = None
