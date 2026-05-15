"""tight_flag_setup 알고리즘 검증 — 6개 fixture (true 3, false 3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals.tight_flag_setup import detect_tight_flag


def make_bars(highs, lows, closes, volumes) -> pd.DataFrame:
    n = len(highs)
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=pd.date_range("2026-05-08 09:00", periods=n, freq="5min", tz="UTC"),
    )


def test_tight_flag_classic_true():
    """전형적 좁은 플래그 — 점진 축소 + 거래량 감소 + 고점 근접."""
    # earlier 6봉: 넓은 range, 거래량 큼
    earlier_h = [100.0, 100.5, 101.0, 100.8, 101.2, 101.5]
    earlier_l = [99.0, 99.0, 99.5, 99.2, 99.5, 99.8]
    earlier_c = [99.5, 100.0, 100.3, 100.0, 100.5, 100.8]
    earlier_v = [10000, 11000, 10500, 10200, 9800, 9500]
    # recent 6봉: 좁은 range, 거래량 감소, 고점 근접
    recent_h = [101.6, 101.55, 101.52, 101.5, 101.5, 101.5]
    recent_l = [101.3, 101.35, 101.4, 101.4, 101.42, 101.45]
    recent_c = [101.5, 101.45, 101.48, 101.45, 101.48, 101.5]
    recent_v = [9000, 8500, 7800, 7200, 6800, 6300]

    bars = make_bars(
        earlier_h + recent_h,
        earlier_l + recent_l,
        earlier_c + recent_c,
        earlier_v + recent_v,
    )
    ok, score = detect_tight_flag(bars, idx=11, n=6)
    assert ok is True
    assert score > 0.4


def test_tight_flag_volatile_false():
    """변동성 큰 봉 — range 점진 축소 안 됨."""
    h = [100, 102, 100, 103, 99, 104, 98, 105, 100, 103, 99, 104]
    l = [98, 99, 97, 100, 96, 100, 95, 101, 97, 100, 95, 100]
    c = [99, 101, 98, 102, 97, 103, 96, 104, 98, 102, 97, 103]
    v = [5000] * 12
    bars = make_bars(h, l, c, v)
    ok, _ = detect_tight_flag(bars, idx=11, n=6)
    assert ok is False


def test_tight_flag_volume_increasing_false():
    """range는 좁지만 거래량 증가 — false."""
    earlier_h = [100, 100.4, 100.8, 100.5, 100.9, 101.2]
    earlier_l = [99, 99.2, 99.5, 99.0, 99.5, 99.8]
    earlier_c = [99.5, 99.8, 100.2, 99.8, 100.2, 100.5]
    earlier_v = [5000, 5500, 5300, 5200, 5400, 5600]
    recent_h = [101.3, 101.32, 101.34, 101.36, 101.38, 101.4]
    recent_l = [101.2, 101.22, 101.24, 101.26, 101.28, 101.3]
    recent_c = [101.25, 101.27, 101.3, 101.32, 101.35, 101.38]
    # volume INCREASING in recent
    recent_v = [6000, 7000, 8000, 9000, 10000, 11000]
    bars = make_bars(
        earlier_h + recent_h,
        earlier_l + recent_l,
        earlier_c + recent_c,
        earlier_v + recent_v,
    )
    ok, _ = detect_tight_flag(bars, idx=11, n=6)
    assert ok is False


def test_tight_flag_far_from_high_false():
    """range 좁고 거래량 감소하지만 마지막 close가 고점에서 멀리 — false."""
    earlier_h = [100.5, 101.0, 100.8, 101.2, 100.9, 101.3]
    earlier_l = [99.5, 99.7, 99.4, 99.8, 99.5, 99.9]
    earlier_c = [100.0, 100.5, 100.2, 100.5, 100.2, 100.6]
    earlier_v = [9000, 9500, 9300, 9200, 9000, 9100]
    # 좁은 range, 거래량 감소, 그러나 마지막 close가 고점에서 1% 이상 떨어짐
    recent_h = [101.4, 101.42, 101.4, 101.41, 101.4, 101.4]
    recent_l = [101.0, 101.0, 101.0, 101.0, 101.0, 100.0]
    recent_c = [101.2, 101.1, 101.05, 101.0, 100.5, 100.0]  # 마지막이 100.0 (고점 101.42에서 1.4% ↓)
    recent_v = [8500, 8000, 7500, 7000, 6500, 6000]
    bars = make_bars(
        earlier_h + recent_h,
        earlier_l + recent_l,
        earlier_c + recent_c,
        earlier_v + recent_v,
    )
    ok, _ = detect_tight_flag(bars, idx=11, n=6)
    assert ok is False


def test_tight_flag_insufficient_bars():
    """min_bars 미만 — 항상 false."""
    bars = make_bars([100, 101, 102], [99, 100, 101], [99.5, 100.5, 101.5], [1000, 1000, 1000])
    ok, score = detect_tight_flag(bars, idx=2, n=6)
    assert ok is False
    assert score == 0.0


def test_tight_flag_score_is_clamped():
    """tightness_score는 0~1 범위."""
    # 임의의 입력
    h = list(range(100, 124))[:12]
    l = [v - 1 for v in h]
    c = [v - 0.5 for v in h]
    v = [1000] * 12
    bars = make_bars(h, l, c, v)
    _, score = detect_tight_flag(bars, idx=11, n=6)
    assert 0.0 <= score <= 1.0
