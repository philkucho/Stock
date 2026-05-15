"""Webull adapter 설정 DTO.

NautilusTrader는 어댑터 설정을 frozen msgspec 기반 NautilusConfig 클래스로 받음.
모든 *Config는 frozen=True 로 선언되어 런타임 변형이 불가능 (TradingNode 안전성 보장).

레퍼런스:
- venv/Lib/site-packages/nautilus_trader/adapters/interactive_brokers/config.py
- venv/Lib/site-packages/nautilus_trader/common/config.py  (InstrumentProviderConfig)
- venv/Lib/site-packages/nautilus_trader/live/config.py    (LiveDataClientConfig, LiveExecClientConfig)
"""

from __future__ import annotations

import os
from typing import Literal

from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveDataClientConfig,
    LiveExecClientConfig,
    NautilusConfig,
)


WebullRegion = Literal["us", "hk", "jp"]


def _mask(value: str | None, head: int = 4, tail: int = 2) -> str:
    if not value:
        return "None"
    if len(value) <= head + tail:
        return "*" * len(value)
    return f"{value[:head]}{'*' * (len(value) - head - tail)}{value[-tail:]}"


class WebullCredentials(NautilusConfig, frozen=True):
    """Webull OpenAPI 인증 정보.

    Paper 모드와 Live 모드는 동일한 키 페어를 공유하지만, 클라이언트 단에서
    base URL 분기와 account 선택이 달라짐.
    """

    app_key: str
    app_secret: str
    region: WebullRegion = "us"
    paper: bool = True

    def __repr__(self) -> str:
        return (
            f"WebullCredentials(app_key={_mask(self.app_key)}, "
            f"app_secret={_mask(self.app_secret)}, "
            f"region='{self.region}', paper={self.paper})"
        )

    @classmethod
    def from_env(cls) -> WebullCredentials:
        """환경 변수에서 로드. python-dotenv로 .env를 미리 load 했다는 가정."""
        app_key = os.environ.get("WEBULL_APP_KEY")
        app_secret = os.environ.get("WEBULL_APP_SECRET")
        if not app_key or not app_secret:
            raise RuntimeError(
                "WEBULL_APP_KEY / WEBULL_APP_SECRET 환경변수가 설정되지 않았습니다. "
                ".env 파일을 확인하세요."
            )
        region = os.environ.get("WEBULL_REGION", "us")
        if region not in ("us", "hk", "jp"):
            raise RuntimeError(f"WEBULL_REGION 값이 잘못됨: {region!r} (us/hk/jp 중 하나)")
        paper = os.environ.get("WEBULL_PAPER", "true").strip().lower() == "true"
        return cls(
            app_key=app_key,
            app_secret=app_secret,
            region=region,
            paper=paper,
        )


class WebullInstrumentProviderConfig(InstrumentProviderConfig, frozen=True):
    """Webull InstrumentProvider 설정.

    Webull은 전체 종목 카탈로그 API를 제공하지 않으므로 `load_all=True`는 미지원.
    실제 거래할 종목들을 `load_ids`로 명시.
    """

    pass


class WebullDataClientConfig(LiveDataClientConfig, frozen=True):
    """LiveMarketDataClient 설정.

    HTTP 폴링 (시세 스냅샷 / 히스토리 bar) + MQTT 스트리밍 (실시간 quote tick) 동시 운영.
    """

    credentials: WebullCredentials | None = None
    instrument_provider: WebullInstrumentProviderConfig = WebullInstrumentProviderConfig()
    http_timeout: int = 30
    mqtt_keepalive: int = 60


class WebullExecClientConfig(LiveExecClientConfig, frozen=True):
    """LiveExecutionClient 설정.

    HTTP (주문 제출/취소/조회) + gRPC (주문 상태 이벤트 푸시) 동시 운영.
    `account_id`가 None이면 `get_app_subscriptions()`의 첫 계좌 사용.
    `token_refresh_interval`은 access token 만료 (~1시간) 대비 50분 디폴트.
    """

    credentials: WebullCredentials | None = None
    instrument_provider: WebullInstrumentProviderConfig = WebullInstrumentProviderConfig()
    account_id: str | None = None
    token_refresh_interval: int = 3000


# Paper/Live 분기는 credentials.paper 로 통제 → 클라이언트 내부에서 base_url 선택:
#   paper: api-demo.webull.com (확정 URL은 SDK endpoint resolver가 결정)
#   live:  api.webull.com
