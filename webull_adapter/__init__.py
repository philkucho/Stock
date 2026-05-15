"""Webull broker adapter for NautilusTrader.

NautilusTrader는 Webull을 네이티브로 지원하지 않으므로 직접 작성.
구현 우선순위 (Day 4~7 작업):
1. providers.py — InstrumentProvider (HTTP 기반 종목 메타데이터)
2. data.py       — LiveMarketDataClient (HTTP 폴링 + MQTT 시세 스트리밍)
3. execution.py  — LiveExecutionClient (HTTP 주문 + gRPC 이벤트 스트림)
4. config.py     — *Config DTO들 (paper/live 분기, 토큰 관리)
5. factories.py  — LiveDataClientFactory / LiveExecClientFactory (조립)

레퍼런스 (pip 설치된 site-packages 경로):
- venv/Lib/site-packages/nautilus_trader/adapters/_template/  — 인터페이스 스캐폴드
- venv/Lib/site-packages/nautilus_trader/adapters/interactive_brokers/  — 미국주식 실제 구현

Webull 3개 프로토콜:
- HTTP   (webullsdkmdata, webullsdktrade): 종목 메타, 계좌, 주문 제출/취소
- MQTT   (webullsdkquotescore + paho-mqtt): 실시간 시세 (bid/ask)
- gRPC   (webullsdktradeeventscore + grpcio): 주문 상태 이벤트 푸시
"""

from nautilus_trader.model.identifiers import Venue

WEBULL_VENUE = Venue("WEBULL")
