"""Webull LiveMarketDataClient.

Day 4 범위: HTTP 히스토리 bar 조회 (`_request_bars`) + connect/disconnect.
실시간 시세 스트림(MQTT)과 quote/trade tick HTTP는 다음 단계(Day 5~7)에서 추가.

NautilusTrader baseline의 `_subscribe_*` / `_request_quote_ticks` / `_request_trade_ticks`
는 호출되면 베이스 클래스가 자동으로 NotImplementedError 발생시키므로 여기선 override 하지 않음.

레퍼런스:
- venv/Lib/site-packages/nautilus_trader/adapters/interactive_brokers/data.py
- venv/Lib/site-packages/nautilus_trader/live/data_client.py  (LiveMarketDataClient 베이스)
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.data.messages import RequestBars
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.objects import Price, Quantity

from webullsdkmdata.common.category import Category
from webullsdkmdata.common.timespan import Timespan
from webullsdkmdata.quotes.market_data import MarketData as WebullMarketDataApi

from webull_adapter import WEBULL_VENUE
from webull_adapter.config import WebullDataClientConfig
from webull_adapter.providers import WebullInstrumentProvider


# Webull은 응답 시각을 ISO 8601 with offset 으로 반환: "2026-05-07T01:09:00.000+0000"
_WEBULL_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


def _bar_spec_to_timespan(spec: BarSpecification) -> Timespan:
    """NautilusTrader BarSpecification → Webull Timespan 매핑.

    NautilusTrader는 step=60 MINUTE 같은 케이스를 BarSpecification 생성 시점에
    이미 거부 (validate_step). 그래서 여기선 그쪽이 통과시켜주는 조합만 매핑.
    """
    agg, step = spec.aggregation, spec.step
    if agg == BarAggregation.MINUTE:
        return {1: Timespan.M1, 5: Timespan.M5, 15: Timespan.M15, 30: Timespan.M30}.get(step) or _unsupported(spec)
    if agg == BarAggregation.HOUR:
        return {1: Timespan.M60, 2: Timespan.M120, 4: Timespan.M240}.get(step) or _unsupported(spec)
    if step == 1:
        return {
            BarAggregation.DAY: Timespan.D,
            BarAggregation.WEEK: Timespan.W,
            BarAggregation.MONTH: Timespan.M,
            BarAggregation.YEAR: Timespan.Y,
        }.get(agg) or _unsupported(spec)
    return _unsupported(spec)


def _unsupported(spec: BarSpecification):
    raise ValueError(f"Webull이 지원하지 않는 BarSpecification: {spec}")


class WebullDataClient(LiveMarketDataClient):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        http_client: WebullMarketDataApi,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: WebullInstrumentProvider,
        config: WebullDataClientConfig,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(WEBULL_VENUE.value),
            venue=WEBULL_VENUE,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
        )
        self._http = http_client

    async def _connect(self) -> None:
        await self._instrument_provider.initialize()

    async def _disconnect(self) -> None:
        # HTTP는 stateless — 별도 종료 작업 없음. MQTT 추가 시 여기서 disconnect.
        return

    async def _request_bars(self, request: RequestBars) -> None:
        bar_type: BarType = request.bar_type
        instrument_id = bar_type.instrument_id
        symbol = instrument_id.symbol.value

        instrument = self._cache.instrument(instrument_id)
        if instrument is None:
            self._log.error(f"Instrument not loaded for {instrument_id} — skip bar request")
            return

        timespan = _bar_spec_to_timespan(bar_type.spec)
        # Webull count는 string. NautilusTrader limit=0 이면 전체 → 디폴트 200.
        count = str(request.limit) if request.limit and request.limit > 0 else "200"

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            self._http.get_history_bar,
            symbol,
            Category.US_STOCK,
            timespan,
            count,
        )
        if response.status_code != 200:
            self._log.error(
                f"Webull get_history_bar 실패: HTTP {response.status_code} body={response.text!r}",
            )
            return

        rows = response.json()
        ts_init = self._clock.timestamp_ns()
        bars = self._parse_bars(rows, bar_type, instrument.price_precision, ts_init)

        self._handle_bars(
            bar_type,
            bars,
            request.id,
            request.start,
            request.end,
            request.params,
        )

    @staticmethod
    def _parse_bars(rows: list[dict], bar_type: BarType, price_precision: int, ts_init: int) -> list[Bar]:
        bars: list[Bar] = []
        for row in rows:
            dt = datetime.strptime(row["time"], _WEBULL_TIME_FORMAT)
            ts_event = int(dt.timestamp() * 1_000_000_000)
            bars.append(
                Bar(
                    bar_type=bar_type,
                    open=Price(float(row["open"]), price_precision),
                    high=Price(float(row["high"]), price_precision),
                    low=Price(float(row["low"]), price_precision),
                    close=Price(float(row["close"]), price_precision),
                    volume=Quantity(float(row["volume"]), 0),  # Equity는 정수 단위
                    ts_event=ts_event,
                    ts_init=ts_init,
                ),
            )
        bars.sort(key=lambda b: b.ts_event)  # Webull은 최신→과거 → 과거→최신으로 재정렬
        return bars
