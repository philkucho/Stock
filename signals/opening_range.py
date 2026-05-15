"""Opening Range Breakout (ORB) + VWAP + intraday RVOL — 단타 confirmation 시그널.

09:30~09:44 ET (첫 15 × 1m bars)로 다음 4개 게이트를 평가:
  1. ORB break: current_price > or_high * (1 + threshold)
  2. VWAP above: current_price > session_vwap
  3. Intraday RVOL ≥ threshold (평소 동시간대 거래량 대비)
  4. OR Range ≥ min_range_pct (너무 좁은 range는 fake breakout 위험)

4개 모두 통과한 종목만 단타 entry. Phase 5 (09:45 ET confirm) 에서 호출.

호출자: scripts/intraday_confirm.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

import pandas as pd

from signals.vwap_reclaim import session_vwap

ORB_WINDOW_MINUTES = 15
ORB_BREAK_THRESHOLD = 0.001   # 0.1% above OR high
INTRADAY_RVOL_THRESHOLD = 1.5
MIN_RANGE_PCT = 0.005          # 0.5% minimum OR range
RVOL_LOOKBACK_DAYS = 20

ET_OPEN_TIME = time(9, 30)
ET_ORB_END_TIME = time(9, 45)


@dataclass(frozen=True)
class ORBEvaluation:
    symbol: str
    as_of: datetime
    or_high: float
    or_low: float
    or_range_pct: float
    session_vwap: float
    current_price: float
    intraday_rvol: float
    pass_orb: bool
    pass_vwap: bool
    pass_rvol: bool
    pass_range: bool

    @property
    def all_passed(self) -> bool:
        return self.pass_orb and self.pass_vwap and self.pass_rvol and self.pass_range

    @property
    def fail_reasons(self) -> list[str]:
        reasons: list[str] = []
        if not self.pass_orb:
            reasons.append(f"orb_break price={self.current_price:.2f} <= or_high={self.or_high:.2f}")
        if not self.pass_vwap:
            reasons.append(f"vwap price={self.current_price:.2f} <= vwap={self.session_vwap:.2f}")
        if not self.pass_rvol:
            reasons.append(f"rvol {self.intraday_rvol:.2f}x < {INTRADAY_RVOL_THRESHOLD}x")
        if not self.pass_range:
            reasons.append(f"range {self.or_range_pct*100:.2f}% < {MIN_RANGE_PCT*100:.2f}%")
        return reasons

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "or_high": round(self.or_high, 4),
            "or_low": round(self.or_low, 4),
            "or_range_pct": round(self.or_range_pct, 6),
            "session_vwap": round(self.session_vwap, 4),
            "current_price": round(self.current_price, 4),
            "intraday_rvol": round(self.intraday_rvol, 3),
            "pass_orb": self.pass_orb,
            "pass_vwap": self.pass_vwap,
            "pass_rvol": self.pass_rvol,
            "pass_range": self.pass_range,
            "all_passed": self.all_passed,
            "fail_reasons": self.fail_reasons,
        }


def _to_et_index(bars: pd.DataFrame) -> pd.DataFrame:
    """bars의 timestamp index를 America/New_York 으로 변환 (UTC → ET)."""
    if bars.empty:
        return bars
    idx = bars.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return bars.set_index(idx.tz_convert("America/New_York"))


def slice_opening_range(bars_1m: pd.DataFrame, session_date) -> pd.DataFrame:
    """session_date의 09:30~09:44 ET 분봉 추출 (15 bars).

    bars_1m: 1분봉 DataFrame (UTC tz-aware index 권장).
    """
    if bars_1m is None or bars_1m.empty:
        return pd.DataFrame()
    et = _to_et_index(bars_1m)
    target = pd.Timestamp(session_date).date()
    mask = (
        (et.index.date == target)
        & (et.index.time >= ET_OPEN_TIME)
        & (et.index.time < ET_ORB_END_TIME)
    )
    return et[mask]


def compute_session_bars(bars_1m: pd.DataFrame, session_date) -> pd.DataFrame:
    """session_date의 09:30 이후 모든 분봉 (ORB 포함, VWAP 계산용)."""
    if bars_1m is None or bars_1m.empty:
        return pd.DataFrame()
    et = _to_et_index(bars_1m)
    target = pd.Timestamp(session_date).date()
    mask = (
        (et.index.date == target)
        & (et.index.time >= ET_OPEN_TIME)
    )
    return et[mask]


def compute_intraday_rvol(
    today_or_bars: pd.DataFrame,
    historical_1m: pd.DataFrame,
    lookback_days: int = RVOL_LOOKBACK_DAYS,
) -> float:
    """오늘 첫 15분 누적 거래량 / (직전 N일 동시간대 누적 거래량 median).

    historical_1m: 직전 거래일들의 1m bars (오늘 제외).
    """
    if today_or_bars is None or today_or_bars.empty:
        return 0.0
    today_vol = float(today_or_bars["volume"].sum())
    if today_vol <= 0:
        return 0.0

    if historical_1m is None or historical_1m.empty:
        return 0.0

    et = _to_et_index(historical_1m)
    mask = (et.index.time >= ET_OPEN_TIME) & (et.index.time < ET_ORB_END_TIME)
    or_slice = et[mask]
    if or_slice.empty:
        return 0.0

    daily_or_vols = (
        or_slice.groupby(or_slice.index.date)["volume"].sum().tail(lookback_days)
    )
    if daily_or_vols.empty:
        return 0.0

    median_vol = float(daily_or_vols.median())
    if median_vol <= 0:
        return 0.0

    return today_vol / median_vol


def evaluate_orb(
    symbol: str,
    bars_1m: pd.DataFrame,
    historical_1m: pd.DataFrame | None,
    session_date,
    *,
    orb_break_threshold: float = ORB_BREAK_THRESHOLD,
    rvol_threshold: float = INTRADAY_RVOL_THRESHOLD,
    min_range_pct: float = MIN_RANGE_PCT,
) -> ORBEvaluation | None:
    """ORB+VWAP+RVOL 4-pass 평가. 데이터 부족 시 None."""
    or_bars = slice_opening_range(bars_1m, session_date)
    if or_bars.empty or len(or_bars) < 5:  # 최소 5분봉은 있어야
        return None

    session_bars = compute_session_bars(bars_1m, session_date)
    if session_bars.empty:
        return None

    or_high = float(or_bars["high"].max())
    or_low = float(or_bars["low"].min())
    current_price = float(session_bars["close"].iloc[-1])

    if or_high <= 0 or or_low <= 0 or current_price <= 0:
        return None

    or_range_pct = (or_high - or_low) / current_price if current_price > 0 else 0.0

    vwap_series = session_vwap(session_bars)
    vwap_value = float(vwap_series.iloc[-1]) if not vwap_series.empty else 0.0

    intraday_rvol = compute_intraday_rvol(or_bars, historical_1m)

    pass_orb = current_price > or_high * (1.0 + orb_break_threshold)
    pass_vwap = current_price > vwap_value
    pass_rvol = intraday_rvol >= rvol_threshold
    pass_range = or_range_pct >= min_range_pct

    return ORBEvaluation(
        symbol=symbol,
        as_of=session_bars.index[-1].to_pydatetime().astimezone(timezone.utc),
        or_high=or_high,
        or_low=or_low,
        or_range_pct=or_range_pct,
        session_vwap=vwap_value,
        current_price=current_price,
        intraday_rvol=intraday_rvol,
        pass_orb=pass_orb,
        pass_vwap=pass_vwap,
        pass_rvol=pass_rvol,
        pass_range=pass_range,
    )


def compute_entry_stop_target(
    eval_result: ORBEvaluation,
    *,
    entry_offset: float = 0.05,
    min_r_pct: float = 0.003,
) -> tuple[float, float, float, float] | None:
    """ORB 통과 종목에서 entry/stop/target_1r/target_2r 산정.

    entry  = or_high + entry_offset (penny tick)
    stop   = max(or_low, session_vwap)
    R      = entry - stop
    t1     = entry + R
    t2     = entry + 2R

    R/entry < min_r_pct 인 경우 (stop 너무 가까움) None 반환.
    """
    entry = eval_result.or_high + entry_offset
    stop = max(eval_result.or_low, eval_result.session_vwap)
    if stop >= entry:
        return None
    r = entry - stop
    if r / entry < min_r_pct:
        return None
    t1 = entry + r
    t2 = entry + 2 * r
    return entry, stop, t1, t2
