"""WebSocket 엔드포인트.

NautilusTrader MessageBus → WebSocket 브릿지로 실시간 시세/주문/포지션을 프론트로 푸시.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/feed")
async def feed(websocket: WebSocket) -> None:
    """실시간 시세/주문 이벤트 스트림."""
    await websocket.accept()
    try:
        # TODO Day 7+: NautilusTrader MessageBus 구독 → WebSocket 푸시
        while True:
            await websocket.receive_text()  # ping/keepalive 처리
    except WebSocketDisconnect:
        return
