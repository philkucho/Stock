"""qty_split_50_50 단위 테스트."""

from __future__ import annotations

from broker_adapter.base import qty_split_50_50


def test_qty_zero_or_negative():
    assert qty_split_50_50(0) == (0, 0)
    assert qty_split_50_50(-5) == (0, 0)


def test_qty_one_fallback():
    """qty=1: 1차만 발송, 2차 0 (단일 BRACKET fallback)."""
    assert qty_split_50_50(1) == (1, 0)


def test_qty_two_minimal_split():
    assert qty_split_50_50(2) == (1, 1)


def test_qty_odd_one_priority():
    """홀수: 1차 ceil, 2차 floor."""
    assert qty_split_50_50(3) == (2, 1)
    assert qty_split_50_50(5) == (3, 2)
    assert qty_split_50_50(7) == (4, 3)
    assert qty_split_50_50(9) == (5, 4)


def test_qty_even_balanced():
    """짝수: 균등 분할."""
    assert qty_split_50_50(4) == (2, 2)
    assert qty_split_50_50(10) == (5, 5)
    assert qty_split_50_50(50) == (25, 25)
    assert qty_split_50_50(100) == (50, 50)


def test_sum_equals_input():
    """t1 + t2 == qty 항상 성립 (qty>=1)."""
    for qty in [1, 2, 3, 5, 7, 10, 11, 13, 50, 99, 100, 101, 1000]:
        t1, t2 = qty_split_50_50(qty)
        assert t1 + t2 == qty, f"qty={qty} → ({t1}, {t2}) sum mismatch"


def test_t1_geq_t2():
    """1차 비중 >= 2차 비중 (1차 우선 관행)."""
    for qty in range(1, 100):
        t1, t2 = qty_split_50_50(qty)
        assert t1 >= t2, f"qty={qty} → t1={t1} < t2={t2} 위반"
