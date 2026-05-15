"""1.2 retry + 5.1 advisory lock 검증.

Test 1: retry 분류
  - APIError(status=429) → retryable, 재시도 후 성공 → 성공 path
  - APIError(status=403) → fatal, 즉시 FatalBrokerError raise
  - APIError(status=503) 4회 연속 → RetryableBrokerError raise (max retries)

Test 2: advisory lock
  - 두 _with_advisory_lock("trade", ...) 동시 실행 → 하나는 실행, 다른 하나는 status=locked
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, patch

from dotenv import load_dotenv

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from alpaca.common.exceptions import APIError  # noqa: E402

from broker_adapter.alpaca_adapter import AlpacaAdapter, _SUBMIT_RETRY_BACKOFFS_SEC  # noqa: E402
from broker_adapter.base import (  # noqa: E402
    BracketOrderRequest,
    FatalBrokerError,
    RetryableBrokerError,
)


def _make_api_error(status_code: int) -> APIError:
    """alpaca-py APIError mock — status_code property가 의도대로 반환되도록."""
    err = APIError('{"code":1,"message":"mock"}')
    fake_http = MagicMock()
    fake_http.response.status_code = status_code
    err._http_error = fake_http  # type: ignore[attr-defined]
    return err


async def _make_adapter() -> AlpacaAdapter:
    a = AlpacaAdapter.from_env()
    a._auto_trade_enabled = True  # 강제 ON (test 한정)
    return a


async def test_retry_429_then_success() -> None:
    print("\n[Test 1a] APIError(429) 1회 → 재시도 후 success")
    adapter = await _make_adapter()
    req = BracketOrderRequest(
        symbol="ZZZ", qty=1, side="BUY",
        entry_type="stop_limit", entry_price=10.0, stop_loss_price=9.0,
        take_profit_price=11.0, time_in_force="day",
    )
    fake_order = MagicMock()
    fake_order.id = "fake-id"
    fake_order.symbol = "ZZZ"
    fake_order.side = "OrderSide.BUY"
    fake_order.order_type = "OrderType.STOP_LIMIT"
    fake_order.status = "OrderStatus.ACCEPTED"
    fake_order.qty = 1
    fake_order.filled_qty = 0
    fake_order.filled_avg_price = None
    fake_order.submitted_at = None
    fake_order.filled_at = None
    fake_order.stop_price = 10.0
    fake_order.limit_price = 10.01
    fake_order.legs = []
    fake_order.model_dump = lambda: {}

    call_count = {"n": 0}
    def submit_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _make_api_error(429)
        return fake_order

    with patch.object(adapter._client, "submit_order", side_effect=submit_side_effect):
        # backoff 시간 단축
        with patch.object(__import__("broker_adapter.alpaca_adapter", fromlist=["_SUBMIT_RETRY_BACKOFFS_SEC"]),
                          "_SUBMIT_RETRY_BACKOFFS_SEC", [0.05, 0.05, 0.05]):
            orders = await adapter.place_bracket_order(req)
    assert len(orders) == 1, f"expected 1 order, got {len(orders)}"
    assert call_count["n"] == 2, f"expected 2 attempts, got {call_count['n']}"
    print(f"  PASS — {call_count['n']} attempts, success on retry")


async def test_fatal_403() -> None:
    print("\n[Test 1b] APIError(403) → 즉시 FatalBrokerError")
    adapter = await _make_adapter()
    req = BracketOrderRequest(
        symbol="ZZZ", qty=1, side="BUY",
        entry_type="stop_limit", entry_price=10.0, stop_loss_price=9.0,
        take_profit_price=11.0, time_in_force="day",
    )
    call_count = {"n": 0}
    def submit_side_effect(*args, **kwargs):
        call_count["n"] += 1
        raise _make_api_error(403)

    with patch.object(adapter._client, "submit_order", side_effect=submit_side_effect):
        try:
            await adapter.place_bracket_order(req)
            print("  FAIL — expected FatalBrokerError")
        except FatalBrokerError as exc:
            assert call_count["n"] == 1, f"expected 1 attempt (no retry), got {call_count['n']}"
            print(f"  PASS — fatal raised after {call_count['n']} attempt: {exc}")


async def test_max_retries_503() -> None:
    print("\n[Test 1c] APIError(503) 매번 → RetryableBrokerError (max retries)")
    adapter = await _make_adapter()
    req = BracketOrderRequest(
        symbol="ZZZ", qty=1, side="BUY",
        entry_type="stop_limit", entry_price=10.0, stop_loss_price=9.0,
        take_profit_price=11.0, time_in_force="day",
    )
    call_count = {"n": 0}
    def submit_side_effect(*args, **kwargs):
        call_count["n"] += 1
        raise _make_api_error(503)

    with patch.object(adapter._client, "submit_order", side_effect=submit_side_effect):
        with patch.object(__import__("broker_adapter.alpaca_adapter", fromlist=["_SUBMIT_RETRY_BACKOFFS_SEC"]),
                          "_SUBMIT_RETRY_BACKOFFS_SEC", [0.05, 0.05, 0.05]):
            try:
                await adapter.place_bracket_order(req)
                print("  FAIL — expected RetryableBrokerError")
            except RetryableBrokerError as exc:
                assert call_count["n"] == 4, f"expected 4 attempts (1+3 retries), got {call_count['n']}"
                print(f"  PASS — {call_count['n']} attempts, max retries: {exc}")


async def test_advisory_lock_blocks_concurrent() -> None:
    print("\n[Test 2] 동시 _with_advisory_lock('trade', ...) — 하나만 통과")
    from scripts.daily_pipeline import _with_advisory_lock

    async def slow_task():
        await asyncio.sleep(0.5)
        return {"status": "ok"}

    # 두 task 동시 실행
    results = await asyncio.gather(
        _with_advisory_lock("test_phase_z", slow_task),
        _with_advisory_lock("test_phase_z", slow_task),
    )
    statuses = sorted(r["status"] for r in results)
    if statuses == ["locked", "ok"]:
        print(f"  PASS — one ran, one locked: {[r['status'] for r in results]}")
    else:
        print(f"  FAIL — expected ['locked', 'ok'], got {statuses}")


async def main() -> None:
    await test_retry_429_then_success()
    await test_fatal_403()
    await test_max_retries_503()
    await test_advisory_lock_blocks_concurrent()


if __name__ == "__main__":
    asyncio.run(main())
