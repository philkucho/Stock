"""Composite Voting Strategy.

여러 시그널의 가중합으로 매수/매도 결정. BNF/CIS 같은 거장의
"여러 조건 동시 만족" 식 종합 판단을 코드화한 것.

흐름:
1. 매 봉마다 history(deque)에 누적
2. 활성 시그널들을 일괄 evaluate (vectorized) → 마지막 봉 시점 정수값들
3. 가중합 vote_score = sum(weight[name] * value)
4. score >= buy_threshold 이고 포지션 없음 → 시장가 매수
5. score <= -sell_threshold 또는 stop_loss/take_profit 도달 → 청산

리스크 룰:
- stop_loss_pct: 진입가 대비 -X% 도달 시 청산 (기본 7%)
- take_profit_pct: 진입가 대비 +X% 도달 시 청산 (기본 15%)
- position_size_pct: 매 진입마다 starting_cash의 X% 사용 (기본 10%)

백테스트 동안 stop/profit은 종가 기준 평가 (인트라바 미모델링).
라이브 단계에선 stop order로 동시 제출하도록 추후 업그레이드.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal

import pandas as pd

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import PositiveFloat, PositiveInt, StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy

from signals import SIGNAL_REGISTRY


class CompositeStrategyConfig(StrategyConfig, frozen=True):
    """여러 시그널의 가중 보팅으로 진입/청산.

    active_signals 비어있으면 strategy는 진입 안 함 (안전 디폴트).
    signal_weights에 active_signals 중 일부만 명시되어도 OK — 누락된 건 1.0.
    """

    instrument_id: InstrumentId
    bar_type: BarType

    active_signals: tuple[str, ...] = ()
    signal_weights: dict[str, float] = {}
    buy_threshold: PositiveFloat = 5.0
    sell_threshold: PositiveFloat = 2.0  # score <= -sell_threshold 일 때 청산

    stop_loss_pct: PositiveFloat = 0.07
    take_profit_pct: PositiveFloat = 0.15
    position_size_pct: PositiveFloat = 0.10
    starting_cash: PositiveFloat = 100_000.0

    # 시간 기반 강제 청산 (BNF 보유기간 1~5일 룰). None=무제한.
    # divergence 같은 zone-신호의 락인을 끊는 보호 메커니즘.
    max_hold_bars: PositiveInt | None = None

    # ===== Phase 3 risk layer (모두 optional, 미설정 시 기존 동작 유지) =====

    # Trailing stop: 진입 후 peak 대비 -X% 도달 시 청산.
    # stop_loss_pct와 함께 동작 (둘 중 먼저 트리거되는 쪽). None이면 비활성.
    trailing_stop_pct: float | None = None

    # ATR 기반 손절: 진입가 - ATR(N) * mult 가격에서 청산.
    # 설정되면 stop_loss_pct 보다 우선. None이면 비활성.
    atr_period: PositiveInt = 14
    atr_stop_mult: float | None = None

    # ATR 기반 사이즈: qty = floor( (account × risk_per_trade_pct) / (ATR × atr_stop_mult) ).
    # position_size_pct 보다 우선. None이면 기존 % 사이즈 사용.
    risk_per_trade_pct: float | None = None

    history_size: PositiveInt = 250  # 시그널 계산용 봉 누적 한도
    close_positions_on_stop: bool = True


def _bars_to_df(bars: deque[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [float(b.open) for b in bars],
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
            "volume": [float(b.volume) for b in bars],
        },
        index=pd.DatetimeIndex(
            [pd.Timestamp(b.ts_event, unit="ns", tz="UTC") for b in bars]
        ),
    )


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """Wilder smoothed ATR. 마지막 값만 반환 (진입 시점 사이즈 결정용).

    데이터 부족 시 None.
    """
    if len(df) < period + 1:
        return None
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # Wilder's smoothing = EMA with alpha=1/period
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    if atr.empty or pd.isna(atr.iloc[-1]):
        return None
    return float(atr.iloc[-1])


class CompositeStrategy(Strategy):
    """시그널 가중합 보팅 전략.

    - active_signals 중 모르는 이름이 있으면 on_start에서 즉시 stop (안전 실패).
    - portfolio.is_flat 으로 중복 진입 방지. 한 종목 1포지션.
    """

    def __init__(self, config: CompositeStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self._specs = []
        self._weights: dict[str, float] = {}
        self._max_warmup = 0
        self._bar_history: deque[Bar] = deque(maxlen=config.history_size)
        self._entry_price: float | None = None
        self._entry_bar_count: int | None = None  # 진입 시점의 누적 봉 수 (max_hold 카운트용)
        # Phase 3 risk-layer state
        self._peak_price: float | None = None  # 진입 후 최고가 (trailing stop 기준)
        self._atr_at_entry: float | None = None  # 진입 시점 ATR (사이즈/손절가 결정용)
        self._atr_stop_price: float | None = None  # ATR 기반 손절가 (절대 가격)

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return

        unknown = [n for n in self.config.active_signals if n not in SIGNAL_REGISTRY]
        if unknown:
            self.log.error(f"Unknown signals: {unknown}")
            self.stop()
            return

        self._specs = [SIGNAL_REGISTRY[n] for n in self.config.active_signals]
        self._weights = {n: self.config.signal_weights.get(n, 1.0) for n in self.config.active_signals}
        self._max_warmup = max((s.min_bars for s in self._specs), default=0)

        self.log.info(
            f"Composite: {len(self._specs)} signals active, warmup={self._max_warmup}, "
            f"buy>={self.config.buy_threshold}, sell<=-{self.config.sell_threshold}",
            color=LogColor.CYAN,
        )

        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self._bar_history.append(bar)
        if bar.is_single_price():
            return
        if len(self._bar_history) <= self._max_warmup:
            return
        if not self._specs:
            return

        df = _bars_to_df(self._bar_history)
        idx = len(df) - 1

        score = 0.0
        for spec in self._specs:
            try:
                val = int(spec.evaluate(df).iloc[idx])
            except Exception as exc:  # 시그널 한 개 실패가 전략을 멈추지 않게
                self.log.warning(f"Signal {spec.name} failed: {exc}")
                val = 0
            score += self._weights[spec.name] * val

        instrument_id = self.config.instrument_id
        flat = self.portfolio.is_flat(instrument_id)

        if flat:
            if score >= self.config.buy_threshold:
                self._enter_long(bar, score, df)
        else:
            # peak price 갱신 (trailing stop 기준점)
            cur = float(bar.close)
            if self._peak_price is None or cur > self._peak_price:
                self._peak_price = cur

            if score <= -self.config.sell_threshold:
                self._exit(reason=f"vote {score:+.1f} <= -{self.config.sell_threshold}")
            elif self._atr_or_stop_hit(bar):
                pass  # ATR 손절 / 기본 손절 / 익절
            elif self._trailing_stop_hit(bar):
                pass  # peak 대비 trailing stop
            elif self._max_hold_hit():
                pass  # 시간 기반 청산

    def _enter_long(self, bar: Bar, score: float, df: pd.DataFrame) -> None:
        assert self.instrument is not None
        price = float(bar.close)

        # ATR 계산 (사이즈/손절가에 사용)
        atr = _compute_atr(df, self.config.atr_period) if self.config.atr_stop_mult or self.config.risk_per_trade_pct else None
        atr_stop_price: float | None = None
        if atr is not None and self.config.atr_stop_mult:
            atr_stop_price = price - atr * self.config.atr_stop_mult

        # Position sizing — 우선순위: risk_per_trade_pct > position_size_pct
        if self.config.risk_per_trade_pct and atr_stop_price and atr_stop_price < price:
            risk_per_share = price - atr_stop_price
            account_risk = self.config.starting_cash * self.config.risk_per_trade_pct
            raw_qty = account_risk / risk_per_share
        else:
            cash_for_pos = self.config.starting_cash * self.config.position_size_pct
            raw_qty = cash_for_pos / max(price, 0.01)
        qty_int = max(1, int(raw_qty))

        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(Decimal(qty_int)),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        self._entry_price = price
        self._entry_bar_count = len(self._bar_history)
        self._peak_price = price
        self._atr_at_entry = atr
        self._atr_stop_price = atr_stop_price

        atr_msg = f" atr={atr:.2f} stop={atr_stop_price:.2f}" if atr_stop_price else ""
        self.log.info(
            f"BUY {self.config.instrument_id} qty={qty_int} px≈{price:.2f} vote={score:+.1f}{atr_msg}",
            color=LogColor.GREEN,
        )

    def _atr_or_stop_hit(self, bar: Bar) -> bool:
        """ATR 손절가 (있으면) 또는 % 기반 손절가 / 익절가 체크.

        우선순위: atr_stop_price가 설정되어 있으면 그것을 손절선으로,
        그렇지 않으면 stop_loss_pct 사용. 익절은 항상 take_profit_pct.
        """
        if self._entry_price is None:
            return False
        cur = float(bar.close)
        ret = (cur - self._entry_price) / self._entry_price

        # ATR 기반 손절 우선
        if self._atr_stop_price is not None:
            if cur <= self._atr_stop_price:
                self._exit(reason=f"atr_stop @{self._atr_stop_price:.2f} (ret {ret*100:+.1f}%)")
                return True
        elif ret <= -self.config.stop_loss_pct:
            self._exit(reason=f"stop_loss {ret*100:+.1f}%")
            return True

        if ret >= self.config.take_profit_pct:
            self._exit(reason=f"take_profit {ret*100:+.1f}%")
            return True
        return False

    def _trailing_stop_hit(self, bar: Bar) -> bool:
        """진입 후 peak 대비 -trailing_stop_pct 도달 시 청산.

        설정 안 되어있으면 비활성. peak는 진입 시점부터 추적.
        """
        if self.config.trailing_stop_pct is None or self._peak_price is None or self._entry_price is None:
            return False
        cur = float(bar.close)
        # peak가 진입가보다 높을 때만 trailing 활성 (즉, 이익 구간만 보호)
        if self._peak_price <= self._entry_price:
            return False
        retr = (cur - self._peak_price) / self._peak_price
        if retr <= -self.config.trailing_stop_pct:
            ret = (cur - self._entry_price) / self._entry_price
            self._exit(reason=f"trailing peak={self._peak_price:.2f} ({retr*100:+.1f}% off peak, total {ret*100:+.1f}%)")
            return True
        return False

    def _max_hold_hit(self) -> bool:
        if self.config.max_hold_bars is None or self._entry_bar_count is None:
            return False
        held = len(self._bar_history) - self._entry_bar_count
        if held >= self.config.max_hold_bars:
            self._exit(reason=f"max_hold {held} bars")
            return True
        return False

    def _exit(self, reason: str) -> None:
        self.close_all_positions(self.config.instrument_id)
        self.log.info(
            f"EXIT {self.config.instrument_id} ({reason})",
            color=LogColor.MAGENTA,
        )
        self._entry_price = None
        self._entry_bar_count = None
        self._peak_price = None
        self._atr_at_entry = None
        self._atr_stop_price = None

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_reset(self) -> None:
        self._bar_history.clear()
        self._entry_price = None
        self._entry_bar_count = None
        self._peak_price = None
        self._atr_at_entry = None
        self._atr_stop_price = None
