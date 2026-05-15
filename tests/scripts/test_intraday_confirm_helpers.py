"""intraday_confirm 보조 함수 검증.

Full run_confirm은 DB/yfinance/broker 의존성이 커 통합 테스트로 분리.
여기서는 순수 함수와 sizing 산식만 검증.
"""

from __future__ import annotations

import math

import pytest

from scripts.intraday_confirm import (
    ENTRY_OFFSET,
    INTRADAY_RISK_PCT,
    _gap_penalty,
)


def test_gap_penalty_no_penalty():
    """gap ≤ 5%: penalty 없음."""
    assert _gap_penalty(0.0) == 1.0
    assert _gap_penalty(1.5) == 1.0
    assert _gap_penalty(4.99) == 1.0
    assert _gap_penalty(-2.0) == 1.0  # 마이너스 갭은 hard skip은 별도, sizing은 1.0


def test_gap_penalty_moderate():
    """5% < gap ≤ 10%: ×0.7."""
    assert _gap_penalty(5.01) == 0.7
    assert _gap_penalty(7.0) == 0.7
    assert _gap_penalty(10.0) == 0.7


def test_gap_penalty_hard_skip():
    """gap > 10%: 0.0 (sizing 0, 발송 skip)."""
    assert _gap_penalty(10.01) == 0.0
    assert _gap_penalty(15.0) == 0.0
    assert _gap_penalty(30.0) == 0.0


def test_gap_penalty_none_fallback():
    assert _gap_penalty(None) == 1.0  # type: ignore[arg-type]


def test_sizing_math_basic():
    """size = floor(equity × risk_pct / R) × regime_mult × gap_penalty.

    예: equity=$10,000, risk_pct=0.003=0.3%, R=$1, regime=1.0, gap=0
    → base_shares = floor(30 / 1) = 30
    → qty = 30 × 1.0 × 1.0 = 30
    """
    equity = 10_000.0
    r_per_share = 1.0
    regime_mult = 1.0
    gap = 0.0

    base = math.floor((equity * INTRADAY_RISK_PCT) / r_per_share)
    qty = int(base * regime_mult * _gap_penalty(gap))

    assert base == 30
    assert qty == 30


def test_sizing_neutral_regime_mult():
    """neutral regime mult=0.7 → 30 × 0.7 = 21."""
    equity = 10_000.0
    r_per_share = 1.0
    base = math.floor((equity * INTRADAY_RISK_PCT) / r_per_share)
    qty = int(base * 0.7 * _gap_penalty(0))
    assert qty == 21


def test_sizing_gap_penalty_compound():
    """neutral + gap 7% → 30 × 0.7 × 0.7 = 14.7 → int = 14."""
    equity = 10_000.0
    r_per_share = 1.0
    base = math.floor((equity * INTRADAY_RISK_PCT) / r_per_share)
    qty = int(base * 0.7 * _gap_penalty(7.0))
    assert qty == 14


def test_sizing_hard_skip_zero_qty():
    """gap > 10% → penalty=0 → qty=0 (skip)."""
    equity = 10_000.0
    r_per_share = 1.0
    base = math.floor((equity * INTRADAY_RISK_PCT) / r_per_share)
    qty = int(base * 1.0 * _gap_penalty(12.0))
    assert qty == 0


def test_sizing_tight_stop_high_r():
    """R 너무 작으면 base_shares 폭증 → 위험. 실제로는 ORB stop이 합리적 → 보통 0.5~2% R."""
    equity = 10_000.0
    risk_dollars = equity * INTRADAY_RISK_PCT  # $30
    # R = $0.10 → base = 300, qty 300 → entry × 300 = 큰 노출이지만 risk_usd는 정확히 $30
    base = math.floor(risk_dollars / 0.10)
    assert base == 300
    # risk_usd 확인: shares × R = 300 × 0.10 = $30
    assert base * 0.10 == pytest.approx(30.0)


def test_entry_offset_default():
    """entry = ORB high + ENTRY_OFFSET ($0.05 default)."""
    assert ENTRY_OFFSET == 0.05
    # ORB high $105.00 → entry $105.05
    or_high = 105.0
    entry = or_high + ENTRY_OFFSET
    assert entry == pytest.approx(105.05)


def test_intraday_risk_pct_default():
    """INTRADAY_RISK_PCT 기본값은 0.3% (swing 0.5%보다 낮음 — 단타 빈도 ↑)."""
    assert INTRADAY_RISK_PCT == 0.003
