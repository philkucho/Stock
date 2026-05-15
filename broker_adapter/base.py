"""BrokerAdapter 추상 인터페이스 — 모든 broker 어댑터가 구현해야 할 contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


class BrokerError(Exception):
    """broker 발송 실패 base."""


class RetryableBrokerError(BrokerError):
    """일시적 실패 — retry 후에도 실패 시 던짐 (rate limit, timeout, 5xx)."""


class FatalBrokerError(BrokerError):
    """영구 실패 — retry 무의미 (insufficient_buying_power, validation, 4xx)."""


def qty_split_50_50(qty: int) -> tuple[int, int]:
    """2-tier 부분 청산용 qty 분할.

    1차 우선 (ceil), 2차 후순위 (floor).
    qty=1은 (1, 0) — 1차만 발송, 2차는 단일 BRACKET fallback.
    """
    if qty <= 0:
        return (0, 0)
    if qty == 1:
        return (1, 0)
    return ((qty + 1) // 2, qty // 2)


@dataclass
class AccountSummary:
    account_id: str
    status: str           # ACTIVE / BLOCKED / etc
    cash: float
    equity: float                   # 현재 NAV
    last_equity: float              # 전일 close NAV (daily P/L 계산용)
    buying_power: float
    pattern_day_trader: bool
    trading_blocked: bool
    raw: dict = field(default_factory=dict)

    @property
    def daily_pnl_pct(self) -> float:
        """전일 close 대비 NAV 변동률 (%). last_equity=0 fallback 시 0."""
        if self.last_equity <= 0:
            return 0.0
        return (self.equity - self.last_equity) / self.last_equity * 100.0


@dataclass
class Position:
    symbol: str
    qty: int              # > 0 long, < 0 short
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    raw: dict = field(default_factory=dict)


@dataclass
class AssetInfo:
    """3.2 Trading halt 감지용 — broker가 보고하는 종목 거래 가능 여부."""
    symbol: str
    tradable: bool        # False면 거래 불가 (halt, delisting 등 포함)
    status: str           # "active" | "inactive" 등
    raw: dict = field(default_factory=dict)

    @property
    def is_halted_or_inactive(self) -> bool:
        return (not self.tradable) or self.status.lower() != "active"


@dataclass
class Order:
    order_id: str
    symbol: str
    qty: int
    side: Literal["BUY", "SELL"]
    order_type: str       # market / limit / stop / stop_limit / bracket
    status: str           # new / accepted / filled / canceled / rejected
    filled_qty: int = 0
    filled_avg_price: float | None = None
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    # bracket 컨텍스트 — nested=True 조회 시 부모 entry + children에서 추출.
    entry_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class BracketOrderRequest:
    """Bracket = entry + stop loss + take profit 한 번에 등록 (OCO).

    1-tier (legacy): qty + take_profit_price 1개
    2-tier: is_two_tier=True + target_1_price/qty + target_2_price/qty
            (qty 필드는 1차+2차 합산으로 자동 계산되어 표기용)
    """
    symbol: str
    qty: int
    side: Literal["BUY", "SELL"] = "BUY"
    entry_price: float | None = None       # None이면 market 진입
    entry_type: Literal["market", "limit", "stop_limit"] = "limit"
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    time_in_force: Literal["day", "gtc"] = "day"

    # 2-tier 부분 청산
    is_two_tier: bool = False
    target_1_price: float = 0.0
    target_1_qty: int = 0
    target_2_price: float = 0.0
    target_2_qty: int = 0


class BrokerAdapter(ABC):
    """모든 broker 어댑터가 구현해야 하는 인터페이스."""

    @abstractmethod
    async def get_account(self) -> AccountSummary: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Position | None: ...

    @abstractmethod
    async def get_asset_info(self, symbol: str) -> AssetInfo:
        """3.2 halt 감지용 — broker가 보고하는 종목 거래 가능 여부 조회."""
        ...

    @abstractmethod
    async def get_orders(
        self,
        status: Literal["open", "closed", "all"] = "open",
        *,
        nested: bool = False,
    ) -> list[Order]:
        """nested=True면 bracket parent만 반환하고 children은 parent.legs에 포함.

        UI/리포트는 nested=True로 가독성 확보. 자동매매 내부 로직(cancel·counter)은
        flat=False 기본이 호환되지만, parent만 보는 것이 의미상 더 정확함.
        """
        ...

    @abstractmethod
    async def place_bracket_order(
        self, req: BracketOrderRequest, *, dry_run: bool = False
    ) -> list[Order]:
        """1-tier면 길이 1, 2-tier면 길이 2 (target_1r + target_2r)."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def close_position(self, symbol: str) -> bool: ...

    @abstractmethod
    async def get_order_children(self, parent_order_id: str) -> list[Order]:
        """Bracket parent의 legs (stop_loss + take_profit)를 반환.

        반환된 Order 중 order_type='stop' 또는 'stop_limit' 이 stop loss leg.
        order_type='limit' 이 take profit leg.
        """
        ...

    @abstractmethod
    async def replace_order_stop(self, order_id: str, new_stop_price: float) -> bool:
        """기존 stop order의 stop_price를 갱신 (breakeven trailing 등)."""
        ...

    @abstractmethod
    async def close(self) -> None: ...
