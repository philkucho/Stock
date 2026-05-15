"""Broker adapter — broker-agnostic 자동매매 인터페이스.

지원:
  - alpaca (paper) — Phase 1 검증
  - webull  (live)  — Phase 2 (별도 모듈, 미구현)

환경변수:
  BROKER_ADAPTER=alpaca|webull
  AUTO_TRADE_ENABLED=true|false   ← 마스터 kill switch (false면 dry-run)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from broker_adapter.base import BrokerAdapter


def get_adapter() -> BrokerAdapter:
    """환경변수 기반 어댑터 factory."""
    name = os.environ.get("BROKER_ADAPTER", "alpaca").strip().lower()
    if name == "alpaca":
        from broker_adapter.alpaca_adapter import AlpacaAdapter
        return AlpacaAdapter.from_env()
    elif name == "webull":
        raise NotImplementedError("Webull adapter는 Phase 2에서 구현. 현재 alpaca paper로만 검증.")
    else:
        raise ValueError(f"Unknown BROKER_ADAPTER: {name!r} (alpaca|webull)")
