"""Alpaca paper 어댑터 PoC 테스트.

검증 단계:
  1) Account 조회
  2) Positions 조회 (현재 비어있어야 함)
  3) Open orders 조회
  4) Bracket order dry-run (AUTO_TRADE_ENABLED=false 가정)
  5) (옵션) AUTO_TRADE_ENABLED=true 시 실제 1주 bracket order 테스트

CLI:
  python -m scripts.test_alpaca_adapter
  python -m scripts.test_alpaca_adapter --live   # 실제 1주 발송 (paper $100K이라 안전)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def main(live: bool = False) -> None:
    from broker_adapter import get_adapter
    from broker_adapter.base import BracketOrderRequest

    if live:
        os.environ["AUTO_TRADE_ENABLED"] = "true"

    adapter = get_adapter()

    print("\n[1] Account")
    acc = await adapter.get_account()
    print(f"    {acc.account_id} status={acc.status} equity=${acc.equity:,.2f} bp=${acc.buying_power:,.2f} PDT={acc.pattern_day_trader}")

    print("\n[2] Positions")
    positions = await adapter.get_positions()
    if not positions:
        print("    (none)")
    for p in positions:
        print(f"    {p.symbol:6s} qty={p.qty:>4d} avg=${p.avg_entry_price:.2f} cur=${p.current_price:.2f} pnl=${p.unrealized_pl:+,.2f} ({p.unrealized_pl_pct:+.2f}%)")

    print("\n[3] Open orders")
    orders = await adapter.get_orders(status="open")
    if not orders:
        print("    (none)")
    for o in orders:
        print(f"    {o.order_id[:8]} {o.symbol:6s} {o.side} qty={o.qty} type={o.order_type} status={o.status}")

    # quote 조회 (테스트용 종목 가격)
    print("\n[4] Place bracket order (BUY 1 SPY @ market with stop/tp)")
    # SPY 현재가 가정: $580 (2026-05 기준)
    # 실 가격 기반 entry/stop/tp 계산
    import yfinance as yf
    spy = yf.Ticker("SPY").history(period="1d")
    if spy.empty:
        print("    [warn] SPY price fetch failed, skip")
        return
    cur = float(spy["Close"].iloc[-1])
    entry = round(cur, 2)
    stop = round(cur * 0.97, 2)        # -3% stop
    tp = round(cur * 1.03, 2)          # +3% take profit
    print(f"    SPY current ~${cur:.2f} → entry=${entry} stop=${stop} tp=${tp}")

    req = BracketOrderRequest(
        symbol="SPY",
        qty=1,
        side="BUY",
        entry_type="market",
        stop_loss_price=stop,
        take_profit_price=tp,
        time_in_force="day",
    )
    orders = await adapter.place_bracket_order(req)
    for o in orders:
        print(f"    → 1-tier order: id={o.order_id[:12]}... status={o.status} type={o.order_type}")

    print("\n[5] Place 2-tier bracket order (qty=2 SPY, target_1r=cur+3%, target_2r=cur+6%)")
    t1 = round(cur * 1.03, 2)
    t2 = round(cur * 1.06, 2)
    req2 = BracketOrderRequest(
        symbol="SPY",
        qty=2,
        side="BUY",
        entry_type="market",
        stop_loss_price=stop,
        time_in_force="day",
        is_two_tier=True,
        target_1_price=t1, target_1_qty=1,
        target_2_price=t2, target_2_qty=1,
    )
    orders_2t = await adapter.place_bracket_order(req2)
    for i, o in enumerate(orders_2t, 1):
        print(f"    → 2-tier #{i}: id={o.order_id[:12]}... qty={o.qty} status={o.status}")

    if live:
        print("\n[6] Verify open orders (should now contain bracket orders)")
        orders3 = await adapter.get_orders(status="open")
        for o in orders3:
            print(f"    {o.order_id[:8]} {o.symbol:6s} {o.side} qty={o.qty} type={o.order_type} status={o.status}")

    await adapter.close()
    print("\nOK adapter PoC complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="실제 발송 (AUTO_TRADE_ENABLED=true override)")
    args = parser.parse_args()
    asyncio.run(main(live=args.live))
