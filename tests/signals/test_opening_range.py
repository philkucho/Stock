"""ORB + VWAP + intraday RVOL 시그널 평가 검증."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from signals.opening_range import (
    INTRADAY_RVOL_THRESHOLD,
    MIN_RANGE_PCT,
    ORB_BREAK_THRESHOLD,
    compute_entry_stop_target,
    compute_intraday_rvol,
    evaluate_orb,
    slice_opening_range,
)


def _make_1m_bars(
    session_date: date,
    *,
    or_high: float = 105.0,
    or_low: float = 100.0,
    after_close: float = 106.0,
    or_volume: int = 50_000,
    after_volume: int = 30_000,
    bar_count_or: int = 15,
    bar_count_after: int = 5,
) -> pd.DataFrame:
    """09:30~09:44 ORB + 09:45~ 후속 분봉. UTC tz-aware index."""
    rows = []
    # ORB 9:30~9:44 (15 bars) — increasing from or_low to or_high
    base = pd.Timestamp(session_date, tz="US/Eastern").replace(hour=9, minute=30)
    for i in range(bar_count_or):
        ts = base + pd.Timedelta(minutes=i)
        # Bar progression: first/last span the full or_low/or_high
        if i == 0:
            o, h, l, c = or_low, or_high * 0.5 + or_low * 0.5, or_low, or_low + 1
        elif i == bar_count_or - 1:
            o, h, l, c = or_high - 1, or_high, or_high - 2, or_high - 0.5
        else:
            o = or_low + (or_high - or_low) * (i / bar_count_or)
            c = o + 0.3
            h = c + 0.2
            l = o - 0.2
        rows.append({
            "open": o, "high": h, "low": l, "close": c,
            "volume": or_volume // bar_count_or,
            "ts": ts,
        })
    # 09:45 이후 — after_close 까지 점진 상승
    after_base = base + pd.Timedelta(minutes=bar_count_or)
    last_close = rows[-1]["close"]
    for i in range(bar_count_after):
        ts = after_base + pd.Timedelta(minutes=i)
        c = last_close + (after_close - last_close) * ((i + 1) / bar_count_after)
        rows.append({
            "open": last_close, "high": c + 0.1, "low": last_close - 0.1,
            "close": c, "volume": after_volume // bar_count_after, "ts": ts,
        })
        last_close = c

    df = pd.DataFrame(rows).set_index("ts")
    df.index = df.index.tz_convert("UTC")
    return df


def test_slice_opening_range_filters_correctly():
    today = date(2026, 5, 11)  # Monday
    bars = _make_1m_bars(today)
    or_bars = slice_opening_range(bars, today)
    assert len(or_bars) == 15
    # 9:30 ET ~ 9:44 ET only
    et = or_bars.index
    assert all(t.time().hour == 9 and 30 <= t.time().minute < 45 for t in et)


def test_evaluate_orb_all_pass():
    today = date(2026, 5, 11)
    bars = _make_1m_bars(today, or_high=105.0, or_low=100.0, after_close=106.0)
    # Historical: 5 prior trading days with consistent OR volume (median)
    hist_rows = []
    for d in range(1, 6):
        prior = today - timedelta(days=d * 2)  # workdays approximation
        for m in range(15):
            ts = pd.Timestamp(prior, tz="US/Eastern").replace(hour=9, minute=30) + pd.Timedelta(minutes=m)
            hist_rows.append({
                "open": 100, "high": 100.5, "low": 99.5, "close": 100,
                "volume": 1000,  # daily OR vol = 15_000
                "ts": ts,
            })
    hist = pd.DataFrame(hist_rows).set_index("ts")
    hist.index = hist.index.tz_convert("UTC")

    # Today OR volume = 50_000 → RVOL = 50_000 / 15_000 ≈ 3.33
    evaluation = evaluate_orb("TEST", bars, hist, today)
    assert evaluation is not None
    assert evaluation.or_high == pytest.approx(105.0, abs=1e-6)
    assert evaluation.or_low == pytest.approx(100.0, abs=1e-6)
    assert evaluation.current_price == pytest.approx(106.0, rel=0.01)
    assert evaluation.intraday_rvol > INTRADAY_RVOL_THRESHOLD
    assert evaluation.pass_orb is True
    assert evaluation.pass_vwap is True
    assert evaluation.pass_rvol is True
    assert evaluation.pass_range is True
    assert evaluation.all_passed is True


def test_evaluate_orb_no_break():
    """current_price가 or_high 아래면 pass_orb=False."""
    today = date(2026, 5, 11)
    bars = _make_1m_bars(today, or_high=105.0, or_low=100.0, after_close=104.5)
    evaluation = evaluate_orb("TEST", bars, None, today)
    assert evaluation is not None
    assert evaluation.pass_orb is False


def test_evaluate_orb_range_too_narrow():
    """OR range가 0.5% 미만이면 pass_range=False — 직접 narrow-range fixture 구성."""
    today = date(2026, 5, 11)
    rows = []
    base = pd.Timestamp(today, tz="US/Eastern").replace(hour=9, minute=30)
    for m in range(15):
        ts = base + pd.Timedelta(minutes=m)
        rows.append({
            "open": 100.0, "high": 100.10, "low": 99.95, "close": 100.05,
            "volume": 1000, "ts": ts,
        })
    # 09:45 이후 1봉
    rows.append({
        "open": 100.05, "high": 100.15, "low": 99.95, "close": 100.10,
        "volume": 500, "ts": base + pd.Timedelta(minutes=15),
    })
    bars = pd.DataFrame(rows).set_index("ts")
    bars.index = bars.index.tz_convert("UTC")

    evaluation = evaluate_orb("TEST", bars, None, today)
    assert evaluation is not None
    assert evaluation.or_range_pct < MIN_RANGE_PCT
    assert evaluation.pass_range is False


def test_evaluate_orb_low_volume_fails_rvol():
    """오늘 OR volume이 historical median 대비 낮으면 pass_rvol=False."""
    today = date(2026, 5, 11)
    bars = _make_1m_bars(today, or_volume=10_000)  # 매우 적음
    # historical OR volume = 20_000 평균
    hist_rows = []
    for d in range(1, 6):
        prior = today - timedelta(days=d * 2)
        for m in range(15):
            ts = pd.Timestamp(prior, tz="US/Eastern").replace(hour=9, minute=30) + pd.Timedelta(minutes=m)
            hist_rows.append({
                "open": 100, "high": 100.5, "low": 99.5, "close": 100,
                "volume": 1333,  # OR vol ≈ 20_000
                "ts": ts,
            })
    hist = pd.DataFrame(hist_rows).set_index("ts")
    hist.index = hist.index.tz_convert("UTC")

    evaluation = evaluate_orb("TEST", bars, hist, today)
    assert evaluation is not None
    assert evaluation.intraday_rvol < INTRADAY_RVOL_THRESHOLD
    assert evaluation.pass_rvol is False
    assert evaluation.all_passed is False


def test_compute_intraday_rvol_zero_history():
    """historical 데이터 없으면 0 반환."""
    today = date(2026, 5, 11)
    or_bars = _make_1m_bars(today).iloc[:15]
    rvol = compute_intraday_rvol(or_bars, None)
    assert rvol == 0.0


def test_compute_entry_stop_target_valid():
    today = date(2026, 5, 11)
    bars = _make_1m_bars(today, or_high=105.0, or_low=100.0, after_close=106.0)
    evaluation = evaluate_orb("TEST", bars, None, today)
    assert evaluation is not None
    levels = compute_entry_stop_target(evaluation, entry_offset=0.05)
    assert levels is not None
    entry, stop, t1, t2 = levels
    assert entry == pytest.approx(105.05, abs=1e-3)
    # stop = max(or_low=100, vwap≈102~104). vwap should win.
    assert stop > 100.0
    r = entry - stop
    assert t1 == pytest.approx(entry + r, abs=1e-3)
    assert t2 == pytest.approx(entry + 2 * r, abs=1e-3)


def test_evaluate_orb_returns_none_on_empty():
    today = date(2026, 5, 11)
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert evaluate_orb("TEST", empty, None, today) is None


def test_orb_fail_reasons_listing():
    today = date(2026, 5, 11)
    bars = _make_1m_bars(today, or_high=105.0, or_low=100.0, after_close=104.0)
    evaluation = evaluate_orb("TEST", bars, None, today)
    assert evaluation is not None
    assert not evaluation.all_passed
    reasons = evaluation.fail_reasons
    assert len(reasons) >= 1
    # At least one of (orb_break/vwap/rvol/range) reason mentioned
    assert any("orb_break" in r or "vwap" in r or "rvol" in r or "range" in r for r in reasons)
