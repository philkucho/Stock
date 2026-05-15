"""rvol.compute_rvol + signal 트리거 검증."""

from __future__ import annotations

import pandas as pd

from signals.rvol import GATE_THRESHOLD, WINDOW, compute_rvol
from signals._registry import SIGNAL_REGISTRY


def test_rvol_basic():
    # 20일 평균 1000, 마지막 봉 3000 → RVOL 3
    volumes = [1000] * 21 + [3000]
    bars = pd.DataFrame(
        {
            "open": [100] * len(volumes),
            "high": [101] * len(volumes),
            "low": [99] * len(volumes),
            "close": [100] * len(volumes),
            "volume": volumes,
        }
    )
    rvol = compute_rvol(bars)
    assert rvol.iloc[-1] == 3.0


def test_rvol_signal_threshold():
    """RVOL >= 2 → 시그널 1, 미만 → 0."""
    volumes = [1000] * 21 + [int(GATE_THRESHOLD * 1000) + 1]  # >= threshold
    bars = pd.DataFrame(
        {
            "open": [100] * len(volumes),
            "high": [101] * len(volumes),
            "low": [99] * len(volumes),
            "close": [100] * len(volumes),
            "volume": volumes,
        }
    )
    spec = SIGNAL_REGISTRY["rvol"]
    sig = spec.evaluate(bars)
    assert int(sig.iloc[-1]) == 1

    # below threshold
    volumes_low = [1000] * 21 + [int(GATE_THRESHOLD * 1000) - 1]
    bars_low = pd.DataFrame(
        {
            "open": [100] * len(volumes_low),
            "high": [101] * len(volumes_low),
            "low": [99] * len(volumes_low),
            "close": [100] * len(volumes_low),
            "volume": volumes_low,
        }
    )
    sig_low = spec.evaluate(bars_low)
    assert int(sig_low.iloc[-1]) == 0
