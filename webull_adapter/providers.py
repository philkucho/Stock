"""Webull InstrumentProvider.

Webull HTTP API(`webullsdkmdata.quotes.instrument`)로 종목 메타데이터를 조회해
NautilusTrader `Equity` 인스턴스로 변환.

제약:
- Webull은 전체 종목 카탈로그 API를 제공하지 않음 → `load_all_async`는 `NotImplementedError`
- 응답에 tick_size / lot_size 정보가 없음 → US 일반주식 디폴트(0.01, lot=1) 사용
- penny stocks(< $1, tick=0.0001)는 이번 단계에서 미지원 (추후 snapshot 응답에서 가져올 예정)

레퍼런스: venv/Lib/site-packages/nautilus_trader/adapters/interactive_brokers/providers.py
"""

from __future__ import annotations

import asyncio

from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency, Price, Quantity

from webullsdkmdata.common.category import Category
from webullsdkmdata.quotes.instrument import Instrument as WebullInstrumentApi

from webull_adapter.config import WebullInstrumentProviderConfig


# US 일반 주식 디폴트
_PRICE_PRECISION = 2
_PRICE_INCREMENT = Price.from_str("0.01")
_LOT_SIZE = Quantity.from_int(1)


class WebullInstrumentProvider(InstrumentProvider):
    def __init__(
        self,
        http_client: WebullInstrumentApi,
        clock: LiveClock,
        config: WebullInstrumentProviderConfig,
    ) -> None:
        super().__init__(config=config)
        self._http = http_client
        self._clock = clock

    async def load_all_async(self, filters: dict | None = None) -> None:
        raise NotImplementedError(
            "Webull은 전체 종목 카탈로그 API를 제공하지 않습니다. "
            "WebullInstrumentProviderConfig(load_ids=...)로 종목을 명시하세요.",
        )

    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        if not instrument_ids:
            return

        symbols_csv = ",".join(iid.symbol.value for iid in instrument_ids)
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            self._http.get_instrument,
            symbols_csv,
            Category.US_STOCK,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Webull get_instrument 실패: HTTP {response.status_code} body={response.text!r}",
            )

        body = response.json()
        if not isinstance(body, list):
            raise RuntimeError(f"예상치 못한 응답 형식 (list 아님): {body!r}")

        # Webull은 잘못된 심볼이 섞이면 HTTP 417 ServerException으로 거부하므로
        # 200 응답이라면 요청한 모든 심볼이 응답에 들어있다고 가정해도 안전.
        by_symbol = {row["symbol"]: row for row in body}
        ts = self._clock.timestamp_ns()
        for iid in instrument_ids:
            self.add(self._build_equity(iid, by_symbol[iid.symbol.value], ts))

    async def load_async(
        self,
        instrument_id: InstrumentId,
        filters: dict | None = None,
    ) -> None:
        await self.load_ids_async([instrument_id], filters)

    @staticmethod
    def _build_equity(instrument_id: InstrumentId, row: dict, ts: int) -> Equity:
        return Equity(
            instrument_id=instrument_id,
            raw_symbol=Symbol(row["symbol"]),
            currency=Currency.from_str(row["currency"]),
            price_precision=_PRICE_PRECISION,
            price_increment=_PRICE_INCREMENT,
            lot_size=_LOT_SIZE,
            ts_event=ts,
            ts_init=ts,
            info={
                "webull_instrument_id": row.get("instrument_id"),
                "webull_exchange_code": row.get("exchange_code"),
                "name": row.get("name"),
            },
        )
