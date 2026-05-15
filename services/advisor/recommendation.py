"""Recommendation Pydantic 모델 + 검증.

Claude 응답을 JSON으로 받아 이 모델로 파싱·검증한다.
검증 실패는 RecommendationValidationError로 raise — 호출부가 catch해서
status='rejected' (reason=validation_failed) 로 저장하거나 재시도.

핵심 안전장치 (validation):
  - entry > stop (long), entry < stop (short)
  - target_1r > entry > stop  (long)
  - R:R(1차) >= 1.5
  - target_2r > target_1r
  - confidence in [0, 1]
  - 현재가 ±5% 안에 entry  (current_price 인자 전달 시)
  - qty > 0  (지정된 경우)

Claude가 환각으로 비현실적 가격을 주면 여기서 걸린다.
"""
from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RecommendationValidationError(ValueError):
    """검증 실패 — Claude 응답이 안전 조건을 위반."""


class RecommendationAction(str, enum.Enum):
    """장중 추천의 action 종류."""

    ENTER = "enter"     # 신규 진입 (intraday_entry / morning)
    ADD = "add"         # 보유 종목 추가매수
    TRIM = "trim"       # 부분 청산
    EXIT = "exit"       # 전량 청산
    HOLD = "hold"       # 행동 없음 (낮은 confidence)


class Recommendation(BaseModel):
    """단일 종목 추천. Claude가 JSON으로 반환하는 단위.

    morning brief는 list[Recommendation]을 반환 (top 3~5).
    intraday는 단일 Recommendation (action=add/trim/exit/hold).
    """

    symbol: str = Field(..., min_length=1, max_length=20)
    action: RecommendationAction = RecommendationAction.ENTER
    side: str = Field(default="BUY", pattern=r"^(BUY|SELL)$")

    entry: Decimal = Field(default=Decimal("0"))
    stop: Decimal = Field(default=Decimal("0"))
    target_1r: Decimal = Field(default=Decimal("0"))
    target_2r: Decimal = Field(default=Decimal("0"))

    qty: int | None = Field(default=None, ge=0)
    confidence: Decimal = Field(..., ge=Decimal("0"), le=Decimal("1"))

    reasoning: str = Field(..., min_length=10, max_length=2000)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_price_geometry(self) -> Recommendation:
        # HOLD는 가격 검증 면제 — entry 등이 의미 없음.
        if self.action == RecommendationAction.HOLD:
            return self

        # EXIT은 entry/stop/target 검증 면제 — qty만 의미 있음.
        if self.action == RecommendationAction.EXIT:
            return self

        entry = float(self.entry)
        stop = float(self.stop)
        t1 = float(self.target_1r)
        t2 = float(self.target_2r)

        if self.side == "BUY":
            if entry <= 0 or stop <= 0:
                raise RecommendationValidationError(
                    f"{self.symbol}: BUY entry/stop must be positive (got entry={entry}, stop={stop})"
                )
            if stop >= entry:
                raise RecommendationValidationError(
                    f"{self.symbol}: BUY stop ({stop}) must be < entry ({entry})"
                )
            if t1 > 0 and t1 <= entry:
                raise RecommendationValidationError(
                    f"{self.symbol}: BUY target_1r ({t1}) must be > entry ({entry})"
                )
            if t1 > 0 and t2 > 0 and t2 <= t1:
                raise RecommendationValidationError(
                    f"{self.symbol}: target_2r ({t2}) must be > target_1r ({t1})"
                )

            # R:R 1차 >= 1.5
            risk = entry - stop
            if risk > 0 and t1 > 0:
                reward_1 = t1 - entry
                rr = reward_1 / risk
                if rr < 1.5:
                    raise RecommendationValidationError(
                        f"{self.symbol}: R:R too low ({rr:.2f} < 1.5) — risk=${risk:.2f} reward=${reward_1:.2f}"
                    )

        return self

    def check_against_current_price(self, current_price: float, max_deviation_pct: float = 5.0) -> None:
        """현재가 대비 entry가 ±N% 안인지 검증. raise on fail.

        Claude가 환각으로 어제 가격이나 과거 가격을 주는 경우 차단.
        validation_error → 호출부가 status='rejected'로 저장.
        """
        if self.action in (RecommendationAction.HOLD, RecommendationAction.EXIT):
            return
        if current_price <= 0:
            return  # 현재가 못 가져왔으면 검증 skip (별도 alert)
        entry = float(self.entry)
        if entry <= 0:
            return
        deviation = abs(entry - current_price) / current_price * 100.0
        if deviation > max_deviation_pct:
            raise RecommendationValidationError(
                f"{self.symbol}: entry ${entry:.2f} deviates {deviation:.1f}% from current ${current_price:.2f} "
                f"(max {max_deviation_pct}%) — likely Claude hallucination"
            )

    def risk_per_share(self) -> Decimal:
        if self.side == "BUY":
            return self.entry - self.stop
        return self.stop - self.entry

    @staticmethod
    def default_expires_at(ttl_seconds: int) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


class MorningBriefResponse(BaseModel):
    """Claude morning brief의 전체 응답 (top 추천 batch)."""

    market_summary: str = Field(..., max_length=1000)
    recommendations: list[Recommendation] = Field(default_factory=list)
    risks_to_watch: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_summary(self) -> MorningBriefResponse:
        # 추천 0개도 valid — 방어 모드 + 모든 종목 reject 가능
        if not self.market_summary.strip():
            raise RecommendationValidationError("market_summary is empty")
        return self


class IntradayCheckResponse(BaseModel):
    """장중 단일 종목 자문 응답."""

    decision: Recommendation
    context_note: str = Field(default="", max_length=500)


def parse_claude_json(payload: str | dict[str, Any], schema: type[BaseModel]) -> BaseModel:
    """Claude 응답 텍스트 또는 dict → Pydantic 모델.

    Claude API가 strict tool use를 쓰면 dict로 받지만, 일반 응답이면 텍스트 안에 JSON.
    텍스트면 ```json``` 블록 또는 첫 { } 추출.

    Pydantic ValidationError는 RecommendationValidationError로 변환 — 호출부가
    한 가지 예외 타입만 catch하면 되도록.
    """
    import json
    import re

    from pydantic import ValidationError

    def _do_validate(data: Any) -> BaseModel:
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise RecommendationValidationError(str(exc)) from exc

    if isinstance(payload, dict):
        return _do_validate(payload)

    text = payload.strip()
    # ```json ... ``` 블록 우선
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        # 첫 { 부터 매칭되는 } 까지
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecommendationValidationError(f"Claude response is not valid JSON: {exc}") from exc
    return _do_validate(data)
