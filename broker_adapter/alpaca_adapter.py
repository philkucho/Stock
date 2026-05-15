"""Alpaca paper trading 어댑터 — Phase 1 검증용.

alpaca-py SDK 사용. SDK는 동기이므로 asyncio.to_thread로 비동기 래핑.

환경변수:
  ALPACA_API_KEY          — API Key ID (PK...)
  ALPACA_API_SECRET       — Secret Key
  ALPACA_BASE_URL         — https://paper-api.alpaca.markets (paper)
                              또는 https://api.alpaca.markets (live, 미사용)
  AUTO_TRADE_ENABLED      — true면 실제 발송, false면 dry-run (로그만)

Phase 1은 paper만 사용. live URL 사용 자체를 코드 레벨에서 차단.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Literal

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from broker_adapter.base import (
    AccountSummary,
    AssetInfo,
    BracketOrderRequest,
    BrokerAdapter,
    FatalBrokerError,
    Order,
    Position,
    RetryableBrokerError,
)

# exponential backoff: 첫 시도 즉시, 이후 0.5s/2s/5s 대기 = 총 4 attempts.
_SUBMIT_RETRY_BACKOFFS_SEC = [0.5, 2.0, 5.0]

logger = logging.getLogger(__name__)


def _penny(price: float) -> str:
    """SEC 규정: $1 이상 주식의 모든 limit/stop price는 penny ($0.01) 단위."""
    return f"{round(float(price), 2):.2f}"


class AlpacaAdapter(BrokerAdapter):
    """Alpaca paper trading 어댑터."""

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        if not paper:
            raise RuntimeError(
                "Phase 1은 paper only. live 사용은 Webull 전환 후 진행. "
                "Alpaca live 차단됨."
            )
        self._client = TradingClient(api_key, api_secret, paper=True)
        self._auto_trade_enabled = (
            os.environ.get("AUTO_TRADE_ENABLED", "false").strip().lower() == "true"
        )
        logger.info(
            "AlpacaAdapter initialized (paper=True, auto_trade=%s)",
            self._auto_trade_enabled,
        )

    @classmethod
    def from_env(cls) -> AlpacaAdapter:
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_API_SECRET")
        if not key or not secret:
            raise RuntimeError(
                "ALPACA_API_KEY / ALPACA_API_SECRET 환경변수가 없습니다. .env 확인."
            )
        # Phase 1: live URL 사용 시 hard fail
        base_url = os.environ.get("ALPACA_BASE_URL", "")
        if "paper-api" not in base_url and base_url:
            raise RuntimeError(
                f"ALPACA_BASE_URL이 paper가 아닙니다: {base_url!r}. "
                "Phase 1은 paper-api.alpaca.markets만 허용."
            )
        return cls(api_key=key, api_secret=secret, paper=True)

    # ─────────── 조회 ───────────

    async def get_account(self) -> AccountSummary:
        acc = await asyncio.to_thread(self._client.get_account)
        # last_equity: 전일 close NAV (Alpaca 제공). 없으면 현재 equity로 fallback (=daily PnL 0)
        try:
            last_equity = float(acc.last_equity) if getattr(acc, "last_equity", None) else float(acc.equity)
        except Exception:
            last_equity = float(acc.equity)
        return AccountSummary(
            account_id=acc.account_number,
            status=str(acc.status).split(".")[-1],
            cash=float(acc.cash),
            equity=float(acc.equity),
            last_equity=last_equity,
            buying_power=float(acc.buying_power),
            pattern_day_trader=bool(acc.pattern_day_trader),
            trading_blocked=bool(acc.trading_blocked),
            raw=acc.model_dump() if hasattr(acc, "model_dump") else {},
        )

    async def get_positions(self) -> list[Position]:
        positions = await asyncio.to_thread(self._client.get_all_positions)
        return [self._to_position(p) for p in positions]

    async def get_asset_info(self, symbol: str) -> AssetInfo:
        """Alpaca asset endpoint — tradable + status 조회 (halt 감지용)."""
        asset = await asyncio.to_thread(self._client.get_asset, symbol.upper())
        return AssetInfo(
            symbol=asset.symbol,
            tradable=bool(asset.tradable),
            status=str(asset.status).split(".")[-1].lower(),
            raw=asset.model_dump() if hasattr(asset, "model_dump") else {},
        )

    async def get_position(self, symbol: str) -> Position | None:
        try:
            p = await asyncio.to_thread(self._client.get_open_position, symbol.upper())
            return self._to_position(p)
        except Exception as exc:
            if "position does not exist" in str(exc).lower() or "404" in str(exc):
                return None
            raise

    async def get_orders(
        self,
        status: Literal["open", "closed", "all"] = "open",
        *,
        nested: bool = False,
    ) -> list[Order]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        req = GetOrdersRequest(status=status_map[status], limit=100, nested=nested)
        orders = await asyncio.to_thread(self._client.get_orders, filter=req)
        return [self._to_order(o) for o in orders]

    # ─────────── 주문 ───────────

    async def place_bracket_order(
        self, req: BracketOrderRequest, *, dry_run: bool = False
    ) -> list[Order]:
        """Bracket order — entry + stop loss + take profit.

        1-tier (req.is_two_tier=False): 단일 BRACKET → list 길이 1
        2-tier (req.is_two_tier=True): qty 분할 2개 BRACKET → list 길이 2
            - 1차: target_1_qty주 + target_1_price
            - 2차: target_2_qty주 + target_2_price
            - 1차 성공/2차 실패 시 1차 자동 cancel rollback

        dry_run 또는 AUTO_TRADE_ENABLED=false: 실제 발송 없이 dry_run Order 반환.
        """
        if not req.is_two_tier:
            order = await self._submit_one_bracket(
                symbol=req.symbol,
                qty=req.qty,
                side=req.side,
                entry_price=req.entry_price,
                entry_type=req.entry_type,
                stop_loss_price=req.stop_loss_price,
                take_profit_price=req.take_profit_price,
                time_in_force=req.time_in_force,
                dry_run=dry_run,
            )
            return [order]

        # 2-tier 검증
        if req.target_1_qty <= 0:
            raise ValueError("2-tier: target_1_qty > 0 필수")
        if req.target_1_price <= 0 or req.target_2_price <= 0:
            raise ValueError("2-tier: target_1_price, target_2_price > 0 필수")
        if req.target_1_price >= req.target_2_price:
            raise ValueError(
                f"2-tier: target_1_price ({req.target_1_price}) < target_2_price ({req.target_2_price}) 필수"
            )
        # penny 반올림 후 동일값 충돌
        if _penny(req.target_1_price) == _penny(req.target_2_price):
            raise ValueError(
                f"2-tier: penny 반올림 후 target_1/target_2 동일 (${_penny(req.target_1_price)}). "
                "두 가격 차이가 $0.01 이상 필요."
            )

        # 1차 발송
        order_1 = await self._submit_one_bracket(
            symbol=req.symbol,
            qty=req.target_1_qty,
            side=req.side,
            entry_price=req.entry_price,
            entry_type=req.entry_type,
            stop_loss_price=req.stop_loss_price,
            take_profit_price=req.target_1_price,
            time_in_force=req.time_in_force,
            dry_run=dry_run,
        )

        if req.target_2_qty <= 0:
            # qty=1 fallback: 2차 미발송
            logger.info(
                "[2-tier] %s target_2_qty=0 (qty<2 fallback) — 1차만 발송",
                req.symbol,
            )
            return [order_1]

        # 2차 발송 (실패 시 1차 cancel rollback)
        try:
            order_2 = await self._submit_one_bracket(
                symbol=req.symbol,
                qty=req.target_2_qty,
                side=req.side,
                entry_price=req.entry_price,
                entry_type=req.entry_type,
                stop_loss_price=req.stop_loss_price,
                take_profit_price=req.target_2_price,
                time_in_force=req.time_in_force,
                dry_run=dry_run,
            )
        except Exception as exc:
            logger.exception(
                "[2-tier] %s 2차 발송 실패 — 1차(%s) cancel rollback 시도",
                req.symbol, order_1.order_id[:12],
            )
            if not dry_run and self._auto_trade_enabled and order_1.status != "dry_run":
                await self.cancel_order(order_1.order_id)
            raise RuntimeError(
                f"2-tier 발송 실패: 1차 {order_1.order_id[:12]} rollback 시도. "
                f"원인: {exc}"
            )

        return [order_1, order_2]

    async def _submit_one_bracket(
        self,
        *,
        symbol: str,
        qty: int,
        side: Literal["BUY", "SELL"],
        entry_price: float | None,
        entry_type: Literal["market", "limit", "stop_limit"],
        stop_loss_price: float,
        take_profit_price: float,
        time_in_force: Literal["day", "gtc"],
        dry_run: bool,
    ) -> Order:
        """단일 BRACKET 주문 발송 (내부 헬퍼)."""
        if dry_run or not self._auto_trade_enabled:
            logger.warning(
                "[DRY RUN] bracket order NOT sent: %s qty=%d entry=%s stop=%s tp=%s",
                symbol, qty, entry_price, stop_loss_price, take_profit_price,
            )
            return Order(
                order_id=f"dryrun-{symbol}-{datetime.utcnow().timestamp():.6f}",
                symbol=symbol,
                qty=qty,
                side=side,
                order_type="bracket-dryrun",
                status="dry_run",
                submitted_at=datetime.utcnow(),
                raw={
                    "dry_run": True,
                    "qty": qty,
                    "entry": entry_price,
                    "stop": stop_loss_price,
                    "tp": take_profit_price,
                },
            )

        if stop_loss_price <= 0 or take_profit_price <= 0:
            raise ValueError(
                "Bracket order는 stop_loss_price > 0, take_profit_price > 0 필수"
            )

        side_enum = OrderSide.BUY if side == "BUY" else OrderSide.SELL
        tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

        common_kwargs = dict(
            symbol=symbol.upper(),
            qty=qty,
            side=side_enum,
            time_in_force=tif,
            order_class=OrderClass.BRACKET,
            stop_loss=StopLossRequest(stop_price=_penny(stop_loss_price)),
            take_profit=TakeProfitRequest(limit_price=_penny(take_profit_price)),
        )

        if entry_type == "market":
            order_req = MarketOrderRequest(**common_kwargs)
        elif entry_type == "limit":
            if entry_price is None or entry_price <= 0:
                raise ValueError("limit entry는 entry_price 필수")
            order_req = LimitOrderRequest(limit_price=_penny(entry_price), **common_kwargs)
        elif entry_type == "stop_limit":
            if entry_price is None or entry_price <= 0:
                raise ValueError("stop_limit entry는 entry_price 필수")
            order_req = StopLimitOrderRequest(
                stop_price=_penny(entry_price),
                # 0.1% slippage buffer — 사용자 정책 (2026-05-13): user_fixed plan은
                # 입력 가격에 가깝게 체결. 갭이 0.1% 이상이면 stop만 trigger되고 limit이
                # 못 채워져 미체결되지만, 사용자 의도(정확 가격) 우선.
                limit_price=_penny(entry_price * 1.001),
                **common_kwargs,
            )
        else:
            raise ValueError(f"unknown entry_type: {entry_type!r}")

        logger.info(
            "Submitting bracket order: %s qty=%d entry=%s(%s) stop=%s tp=%s",
            symbol, qty, entry_price, entry_type, stop_loss_price, take_profit_price,
        )

        # Retry: 일시적 장애(rate limit, 5xx, network)는 backoff 후 재시도.
        # 영구 장애(insufficient_buying_power, validation 4xx)는 즉시 fatal raise.
        last_exc: Exception | None = None
        attempts = [0.0] + _SUBMIT_RETRY_BACKOFFS_SEC
        for attempt_idx, backoff in enumerate(attempts):
            if backoff > 0:
                logger.warning(
                    "[retry] submit %s attempt %d/%d after %.1fs (last: %s)",
                    symbol, attempt_idx + 1, len(attempts), backoff, last_exc,
                )
                await asyncio.sleep(backoff)
            try:
                order = await asyncio.to_thread(
                    self._client.submit_order, order_data=order_req
                )
                if attempt_idx > 0:
                    logger.info("[retry] submit %s succeeded on attempt %d", symbol, attempt_idx + 1)
                return self._to_order(order)
            except APIError as exc:
                sc = exc.status_code
                if sc and (sc == 429 or 500 <= sc < 600):
                    last_exc = exc
                    continue
                # 4xx (insufficient_buying_power 403, validation 422 등) — 즉시 fail.
                raise FatalBrokerError(
                    f"Alpaca rejected {symbol} (status={sc}): {exc}"
                ) from exc
            except Exception as exc:
                msg = str(exc).lower()
                if any(k in msg for k in ("timeout", "connection reset", "connection aborted", "temporarily unavailable")):
                    last_exc = exc
                    continue
                # 알 수 없는 예외는 fatal로 보수적 처리 (멋대로 retry하지 않음).
                raise FatalBrokerError(
                    f"unknown error submitting {symbol}: {type(exc).__name__}: {exc}"
                ) from exc

        raise RetryableBrokerError(
            f"max retries exceeded for {symbol} ({len(attempts)} attempts). last: {last_exc}"
        )

    async def get_order(self, order_id: str) -> Order | None:
        """단일 order 조회 (children 미포함). bracket parent의 자체 filled_qty 추적용."""
        try:
            order = await asyncio.to_thread(self._client.get_order_by_id, order_id)
            return self._to_order(order)
        except Exception as exc:
            logger.warning("get_order(%s) failed: %s", order_id, exc)
            return None

    async def get_order_children(self, parent_order_id: str) -> list[Order]:
        """Bracket parent의 legs (stop_loss + take_profit) 반환.

        alpaca-py: GetOrderByIdRequest(nested=True)로 children을 parent.legs에 받음.
        직접 nested=True keyword 전달은 SDK signature와 안 맞음 (2026-05-14 버그 fix).
        """
        from alpaca.trading.requests import GetOrderByIdRequest

        try:
            order = await asyncio.to_thread(
                self._client.get_order_by_id,
                parent_order_id,
                GetOrderByIdRequest(nested=True),
            )
            legs = getattr(order, "legs", None) or []
            return [self._to_order(leg) for leg in legs]
        except Exception as exc:
            logger.warning("get_order_children(%s) failed: %s", parent_order_id, exc)
            return []

    async def replace_order_stop(self, order_id: str, new_stop_price: float) -> bool:
        """stop order의 stop_price 갱신 (breakeven trailing)."""
        if not self._auto_trade_enabled:
            logger.warning("[DRY RUN] replace_order_stop NOT sent: %s -> $%s", order_id, new_stop_price)
            return True
        try:
            from alpaca.trading.requests import ReplaceOrderRequest
            req = ReplaceOrderRequest(stop_price=_penny(new_stop_price))
            await asyncio.to_thread(
                self._client.replace_order_by_id, order_id, order_data=req
            )
            logger.info("Order stop replaced: %s -> $%s", order_id[:12], _penny(new_stop_price))
            return True
        except Exception as exc:
            logger.error("replace_order_stop failed: %s -> $%s — %s", order_id[:12], new_stop_price, exc)
            return False

    async def cancel_order(self, order_id: str) -> bool:
        if not self._auto_trade_enabled:
            logger.warning("[DRY RUN] cancel_order NOT sent: %s", order_id)
            return True
        try:
            await asyncio.to_thread(self._client.cancel_order_by_id, order_id)
            logger.info("Order cancelled: %s", order_id)
            return True
        except Exception as exc:
            logger.error("cancel_order failed: %s — %s", order_id, exc)
            return False

    async def close_position(self, symbol: str) -> bool:
        if not self._auto_trade_enabled:
            logger.warning("[DRY RUN] close_position NOT sent: %s", symbol)
            return True
        try:
            await asyncio.to_thread(self._client.close_position, symbol.upper())
            logger.info("Position closed: %s", symbol)
            return True
        except Exception as exc:
            logger.error("close_position failed: %s — %s", symbol, exc)
            return False

    async def close(self) -> None:
        # alpaca-py TradingClient는 explicit close 불필요
        pass

    # ─────────── 변환 ───────────

    def _to_position(self, p) -> Position:
        return Position(
            symbol=p.symbol,
            qty=int(float(p.qty)),
            avg_entry_price=float(p.avg_entry_price),
            current_price=float(p.current_price),
            market_value=float(p.market_value),
            unrealized_pl=float(p.unrealized_pl),
            unrealized_pl_pct=float(p.unrealized_plpc) * 100,
            raw=p.model_dump() if hasattr(p, "model_dump") else {},
        )

    def _to_order(self, o) -> Order:
        side = "BUY" if str(o.side).endswith("BUY") else "SELL"
        # bracket parent의 entry는 stop_limit이면 stop_price, limit이면 limit_price
        entry_price = None
        if o.stop_price is not None:
            entry_price = float(o.stop_price)
        elif o.limit_price is not None:
            entry_price = float(o.limit_price)
        # children에서 stop_loss / take_profit 추출 (nested=True 호출 시)
        stop_loss_price = None
        take_profit_price = None
        for leg in (getattr(o, "legs", None) or []):
            leg_type = str(leg.order_type).split(".")[-1].lower()
            if leg_type in ("stop", "stop_limit") and leg.stop_price is not None:
                stop_loss_price = float(leg.stop_price)
            elif leg_type == "limit" and leg.limit_price is not None:
                take_profit_price = float(leg.limit_price)
        return Order(
            order_id=str(o.id),
            symbol=o.symbol,
            qty=int(float(o.qty or 0)),
            side=side,
            order_type=str(o.order_type).split(".")[-1].lower(),
            status=str(o.status).split(".")[-1].lower(),
            filled_qty=int(float(o.filled_qty or 0)),
            filled_avg_price=(
                float(o.filled_avg_price) if o.filled_avg_price else None
            ),
            submitted_at=o.submitted_at,
            filled_at=o.filled_at,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            raw=o.model_dump() if hasattr(o, "model_dump") else {},
        )
