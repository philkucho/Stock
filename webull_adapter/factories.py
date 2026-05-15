"""NautilusTrader factory wiring.

NautilusTrader 노드가 어댑터를 인식할 수 있도록 LiveDataClientFactory /
LiveExecClientFactory를 구현. config.py의 *Config를 받아 클라이언트 인스턴스 생성.

레퍼런스: venv/Lib/site-packages/nautilus_trader/adapters/interactive_brokers/factories.py
"""

from __future__ import annotations

# TODO Day 7 구현 (위 4개 파일이 채워진 후 마지막 조립)
#
# from functools import lru_cache
# from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory
#
# from webull_adapter.config import (
#     WebullCredentials, WebullDataClientConfig, WebullExecClientConfig,
# )
# from webull_adapter.data import WebullDataClient
# from webull_adapter.execution import WebullExecutionClient
#
#
# @lru_cache(maxsize=1)
# def get_cached_webull_http_client(credentials: WebullCredentials):
#     # webullsdkcore.client.ApiClient 초기화 + signing 설정
#     ...
#
#
# @lru_cache(maxsize=1)
# def get_cached_webull_mqtt_client(credentials: WebullCredentials):
#     # webullsdkquotescore + paho.mqtt.client.Client 초기화
#     ...
#
#
# @lru_cache(maxsize=1)
# def get_cached_webull_grpc_client(credentials: WebullCredentials):
#     # webullsdktradeeventscore gRPC 채널
#     ...
#
#
# class WebullLiveDataClientFactory(LiveDataClientFactory):
#     @staticmethod
#     def create(loop, name, config: WebullDataClientConfig, msgbus, cache, clock):
#         http = get_cached_webull_http_client(config.credentials)
#         mqtt = get_cached_webull_mqtt_client(config.credentials)
#         from webull_adapter.providers import WebullInstrumentProvider
#         provider = WebullInstrumentProvider(http, clock, config.instrument_provider)
#         return WebullDataClient(
#             loop=loop, http_client=http, mqtt_client=mqtt,
#             msgbus=msgbus, cache=cache, clock=clock,
#             instrument_provider=provider, config=config,
#         )
#
#
# class WebullLiveExecClientFactory(LiveExecClientFactory):
#     @staticmethod
#     def create(loop, name, config: WebullExecClientConfig, msgbus, cache, clock):
#         http = get_cached_webull_http_client(config.credentials)
#         grpc = get_cached_webull_grpc_client(config.credentials)
#         from webull_adapter.providers import WebullInstrumentProvider
#         provider = WebullInstrumentProvider(http, clock, config.instrument_provider)
#         return WebullExecutionClient(
#             loop=loop, http_client=http, grpc_client=grpc,
#             msgbus=msgbus, cache=cache, clock=clock,
#             instrument_provider=provider, config=config,
#         )
#
#
# 사용 예 (TradingNode 설정):
#   from nautilus_trader.live.node import TradingNode, TradingNodeConfig
#
#   node = TradingNode(config=TradingNodeConfig(
#       trader_id="TRADER-001",
#       data_clients={"WEBULL": WebullDataClientConfig(...)},
#       exec_clients={"WEBULL": WebullExecClientConfig(...)},
#   ))
#   node.add_data_client_factory("WEBULL", WebullLiveDataClientFactory)
#   node.add_exec_client_factory("WEBULL", WebullLiveExecClientFactory)
#   node.build()
#   node.run()
