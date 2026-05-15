"""Webull LiveExecutionClient.

계좌/포지션 조회 + 주문 제출/취소/수정. HTTP(주문 명령) + gRPC(주문 이벤트 스트림).

NautilusTrader 1.221 API:
- _connect / _disconnect (필수)
- _submit_order / _cancel_order / _cancel_all_orders / _modify_order (필수)
- generate_account_state (필수)
- generate_order_status_report / generate_fill_reports / generate_position_status_reports
- generate_order_filled / generate_order_canceled / generate_order_rejected (이벤트 발행 헬퍼)

레퍼런스: venv/Lib/site-packages/nautilus_trader/adapters/interactive_brokers/execution.py
"""

from __future__ import annotations

# TODO Day 6~7 구현 (read-only 우선, _submit_order는 paper에서 검증 후 활성화)
#
# from nautilus_trader.live.execution_client import LiveExecutionClient
# from nautilus_trader.execution.messages import (
#     SubmitOrder, CancelOrder, CancelAllOrders, ModifyOrder
# )
# from nautilus_trader.execution.reports import (
#     OrderStatusReport, FillReport, PositionStatusReport
# )
# from nautilus_trader.model.enums import OrderSide, OrderStatus, TimeInForce
#
# class WebullExecutionClient(LiveExecutionClient):
#     def __init__(self, loop, http_client, grpc_client, msgbus, cache, clock,
#                  instrument_provider, config):
#         super().__init__(...)
#         self._http = http_client       # webullsdktrade
#         self._grpc = grpc_client       # webullsdktradeeventscore
#         self._account_id = config.account_id
#         self._venue_orders: dict = {}  # client_order_id → Webull order_id
#
#     async def _connect(self) -> None:
#         # 1. 계좌 ID 확보 (config에 없으면 list_accounts에서 첫 계좌)
#         # 2. gRPC 채널 연결 (webullsdktradeeventscore)
#         # 3. 주문 이벤트 구독 시작 (백그라운드 task)
#         # 4. 기존 보유 포지션 + 미체결 주문 로드 → cache 반영
#         ...
#
#     async def _disconnect(self) -> None:
#         # gRPC 채널 close, 백그라운드 task 취소
#         ...
#
#     # ── Read-only (Day 6) ────────────────────────────────────────────────
#     async def generate_account_state(self) -> None:
#         # webullsdktrade get_account_balance → AccountState 메시지 발행
#         ...
#
#     async def generate_order_status_reports(self, command) -> list:
#         # 미체결/완료 주문 조회 → OrderStatusReport 리스트
#         ...
#
#     async def generate_position_status_reports(self, command) -> list:
#         # 보유 포지션 조회 → PositionStatusReport 리스트
#         ...
#
#     async def generate_fill_reports(self, command) -> list:
#         # 체결 내역 조회 → FillReport 리스트
#         ...
#
#     # ── 주문 명령 (Day 7+ paper에서 먼저 검증) ──────────────────────────
#     async def _submit_order(self, command: "SubmitOrder") -> None:
#         order = command.order
#         # 1. NautilusTrader Order → Webull payload 변환 (parsing/orders.py 모듈)
#         # 2. http.place_order(payload)
#         # 3. 응답 order_id 저장 → self._venue_orders[client_order_id] = order_id
#         # 4. submitted 이벤트 발행: self.generate_order_submitted(...)
#         # 실패 시: self.generate_order_rejected(reason=...)
#         ...
#
#     async def _cancel_order(self, command: "CancelOrder") -> None:
#         # http.cancel_order(self._venue_orders[command.client_order_id])
#         ...
#
#     async def _cancel_all_orders(self, command: "CancelAllOrders") -> None:
#         # 미체결 조회 후 루프 cancel
#         ...
#
#     # ── gRPC 주문 이벤트 콜백 ────────────────────────────────────────────
#     async def _on_order_event(self, event) -> None:
#         # Webull OrderEvent → NautilusTrader OrderStatus 매핑
#         #   PENDING        → OrderStatus.SUBMITTED
#         #   WORKING/PARTIAL→ OrderStatus.ACCEPTED
#         #   FILLED         → generate_order_filled (FillReport 생성)
#         #   CANCELLED      → generate_order_canceled
#         #   REJECTED       → generate_order_rejected
#         ...
#
# 함정 (Explore agent 분석):
# - Place Order rate limit: 1초당 1건 (App ID 단위) — _submit_order 큐잉/throttle 필요
# - OAuth 토큰 만료: HTTP 401 응답 시 refresh_token 호출 후 재시도
# - Paper trading: 슬리피지 없음, OCO/stop 일부 거부 가능
# - Webull error code → OrderRejectReason 매핑 표 필요 (parsing/errors.py)
# - Money/Price/Quantity 변환: Webull은 str/float, NautilusTrader는 Cython 타입 (precision 주의)
