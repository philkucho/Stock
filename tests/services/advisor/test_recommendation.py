"""services.advisor.recommendation Pydantic 검증 테스트.

핵심 안전장치: Claude 환각 가격을 차단해야 한다.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from services.advisor.recommendation import (
    IntradayCheckResponse,
    MorningBriefResponse,
    Recommendation,
    RecommendationAction,
    RecommendationValidationError,
    parse_claude_json,
)

# Pydantic v2 model_validator는 ValueError를 ValidationError로 wrap하므로
# 두 예외 타입을 모두 직접 검증 케이스에서 허용한다.
_VALIDATION_ERRORS = (RecommendationValidationError, ValidationError)


# ──── 기본 검증 ────


def _valid_buy_kwargs() -> dict:
    return {
        "symbol": "AAPL",
        "action": RecommendationAction.ENTER,
        "side": "BUY",
        "entry": Decimal("184.50"),
        "stop": Decimal("181.20"),
        "target_1r": Decimal("191.10"),
        "target_2r": Decimal("197.70"),
        "confidence": Decimal("0.72"),
        "reasoning": "Strong setup with confluence and consensus tier S.",
    }


def test_valid_buy_recommendation_passes() -> None:
    rec = Recommendation(**_valid_buy_kwargs())
    assert rec.symbol == "AAPL"
    assert float(rec.risk_per_share()) == pytest.approx(3.30, rel=1e-3)


def test_stop_above_entry_rejected() -> None:
    kw = _valid_buy_kwargs()
    kw["stop"] = Decimal("190.00")  # > entry
    with pytest.raises(_VALIDATION_ERRORS, match="stop"):
        Recommendation(**kw)


def test_target_1r_below_entry_rejected() -> None:
    kw = _valid_buy_kwargs()
    kw["target_1r"] = Decimal("183.00")  # < entry
    with pytest.raises(_VALIDATION_ERRORS, match="target_1r"):
        Recommendation(**kw)


def test_target_2r_below_target_1r_rejected() -> None:
    kw = _valid_buy_kwargs()
    kw["target_1r"] = Decimal("190.00")
    kw["target_2r"] = Decimal("188.00")
    with pytest.raises(_VALIDATION_ERRORS, match="target_2r"):
        Recommendation(**kw)


def test_rr_below_1_5_rejected() -> None:
    # entry 184.50, stop 184.00 → risk 0.50
    # target_1r 185.00 → reward 0.50 → R:R 1.0 < 1.5
    kw = _valid_buy_kwargs()
    kw["stop"] = Decimal("184.00")
    kw["target_1r"] = Decimal("185.00")
    kw["target_2r"] = Decimal("186.00")
    with pytest.raises(_VALIDATION_ERRORS, match="R:R"):
        Recommendation(**kw)


def test_negative_entry_rejected() -> None:
    kw = _valid_buy_kwargs()
    kw["entry"] = Decimal("0")
    with pytest.raises(_VALIDATION_ERRORS, match="positive"):
        Recommendation(**kw)


def test_hold_action_skips_price_validation() -> None:
    rec = Recommendation(
        symbol="AAPL",
        action=RecommendationAction.HOLD,
        entry=Decimal("0"),
        stop=Decimal("0"),
        target_1r=Decimal("0"),
        target_2r=Decimal("0"),
        confidence=Decimal("0.5"),
        reasoning="No clear signal — hold for now.",
    )
    assert rec.action == RecommendationAction.HOLD


def test_exit_action_skips_price_validation() -> None:
    rec = Recommendation(
        symbol="NVDA",
        action=RecommendationAction.EXIT,
        side="SELL",
        entry=Decimal("0"),
        stop=Decimal("0"),
        target_1r=Decimal("0"),
        target_2r=Decimal("0"),
        qty=50,
        confidence=Decimal("0.85"),
        reasoning="Earnings miss + guidance cut. Exit immediately.",
    )
    assert rec.qty == 50


# ──── 현재가 검증 ────


def test_current_price_within_band_ok() -> None:
    rec = Recommendation(**_valid_buy_kwargs())
    # entry 184.50, current 185.00 → 0.27% deviation
    rec.check_against_current_price(185.00)


def test_current_price_far_off_rejected() -> None:
    rec = Recommendation(**_valid_buy_kwargs())
    # entry 184.50, current 175.00 → 5.43% deviation, > 5% default
    with pytest.raises(RecommendationValidationError, match="deviates"):
        rec.check_against_current_price(175.00)


def test_current_price_zero_skips_check() -> None:
    rec = Recommendation(**_valid_buy_kwargs())
    rec.check_against_current_price(0.0)  # no raise — 현재가 못 가져왔으면 skip


# ──── 신뢰도 범위 ────


def test_confidence_above_1_rejected() -> None:
    kw = _valid_buy_kwargs()
    kw["confidence"] = Decimal("1.5")
    with pytest.raises(_VALIDATION_ERRORS):
        Recommendation(**kw)


def test_confidence_negative_rejected() -> None:
    kw = _valid_buy_kwargs()
    kw["confidence"] = Decimal("-0.1")
    with pytest.raises(_VALIDATION_ERRORS):
        Recommendation(**kw)


# ──── JSON parsing ────


def test_parse_claude_json_with_fence() -> None:
    text = """
Some preamble from Claude.

```json
{
  "market_summary": "Defensive mode — long blocked due to VIX spike.",
  "recommendations": [],
  "risks_to_watch": ["FOMC at 14:00 ET"]
}
```
"""
    parsed = parse_claude_json(text, MorningBriefResponse)
    assert isinstance(parsed, MorningBriefResponse)
    assert "Defensive" in parsed.market_summary


def test_parse_claude_json_without_fence() -> None:
    text = '{"market_summary": "ok", "recommendations": [], "risks_to_watch": []}'
    parsed = parse_claude_json(text, MorningBriefResponse)
    assert isinstance(parsed, MorningBriefResponse)


def test_parse_claude_json_invalid_raises() -> None:
    with pytest.raises(RecommendationValidationError):
        parse_claude_json("not json at all", MorningBriefResponse)


def test_morning_brief_with_recommendations() -> None:
    text = """{
      "market_summary": "Neutral regime, moderate momentum.",
      "recommendations": [
        {
          "symbol": "AAPL",
          "action": "enter",
          "side": "BUY",
          "entry": 184.50,
          "stop": 181.20,
          "target_1r": 191.10,
          "target_2r": 197.70,
          "qty": null,
          "confidence": 0.72,
          "reasoning": "Strong v10 setup with consensus S, sector momentum positive."
        }
      ],
      "risks_to_watch": []
    }"""
    parsed = parse_claude_json(text, MorningBriefResponse)
    assert isinstance(parsed, MorningBriefResponse)
    assert len(parsed.recommendations) == 1
    assert parsed.recommendations[0].symbol == "AAPL"


def test_intraday_check_response() -> None:
    text = """{
      "decision": {
        "symbol": "NVDA",
        "action": "trim",
        "side": "SELL",
        "entry": 0,
        "stop": 0,
        "target_1r": 0,
        "target_2r": 0,
        "qty": 25,
        "confidence": 0.7,
        "reasoning": "Up 8% intraday, partial trim to lock gains while letting runner go."
      },
      "context_note": "1차 익절 후 잔여 50% 중 절반 추가 트림."
    }"""
    parsed = parse_claude_json(text, IntradayCheckResponse)
    assert isinstance(parsed, IntradayCheckResponse)
    assert parsed.decision.action == RecommendationAction.TRIM
    assert parsed.decision.qty == 25


def test_morning_brief_rejects_invalid_inner_recommendation() -> None:
    # Inner recommendation has stop > entry → 검증 실패
    text = """{
      "market_summary": "ok",
      "recommendations": [
        {
          "symbol": "BAD",
          "action": "enter",
          "side": "BUY",
          "entry": 100,
          "stop": 110,
          "target_1r": 120,
          "target_2r": 130,
          "confidence": 0.6,
          "reasoning": "broken price geometry"
        }
      ],
      "risks_to_watch": []
    }"""
    with pytest.raises((RecommendationValidationError, Exception)):
        parse_claude_json(text, MorningBriefResponse)
