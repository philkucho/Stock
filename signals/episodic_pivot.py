"""Episodic Pivot (EP) — Qullamaggie 셋업 시그널 (2026-06-15 신규).

EP = 실적/뉴스/업그레이드 등 새 정보로 큰 갭업 + 대량 거래량이 터진 종목.
Qullamaggie 원형:
  - 갭 ≥ +8~10% (전일 종가 → 당일 시가/프리마켓)
  - 첫 30분 RVOL ≥ 3x (평소 동시간대/20일 평균 대비)
  - ADR(20) ≥ 4% (저변동 메가캡 자연 배제)
  - 진입: 당일 OR(5분) 고점 돌파 (분봉 있을 때)
  - 청산: 3~5일/2~3R에서 부분익절 → 잔여는 10/20 EMA 추적

이 모듈은 게이트 평가만 담당 (entry/stop/exit 시뮬은 backtests/run_ep.py).
ORB(signals/opening_range.py)와 달리 "큰 갭"이 신호의 핵심 — 메가캡 미세 돌파 아님.

ORB는 2026-06-05 폐기됨. EP는 백테스트 검증 전 라이브 미연결.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import pandas as pd

EP_GAP_MIN = 8.0          # 전일 종가 대비 당일 갭 ≥ +8%
EP_RVOL_MIN = 3.0         # 거래량 / 20일 평균 ≥ 3x
EP_ADR_PCT_MIN = 4.0      # ADR(20) ≥ 4%
EP_ADR_LOOKBACK = 20
EP_OR_WINDOW_MINUTES = 5  # OR = 첫 5분 (09:30~09:34 ET)

ET_OPEN_TIME = time(9, 30)
ET_OR_END_TIME = time(9, 35)


@dataclass(frozen=True)
class EPEvaluation:
    symbol: str
    gap_pct: float
    rvol: float
    adr_pct: float
    prev_close: float
    open_price: float
    or_high: float | None       # 분봉 있을 때만
    or_break_price: float | None  # OR 돌파 확인 가격 (분봉 있을 때만)
    pass_gap: bool
    pass_rvol: bool
    pass_adr: bool
    pass_or_break: bool         # 분봉 없으면 자동 True (일봉 백테스트 모드)
    has_intraday: bool

    @property
    def gates_passed(self) -> bool:
        """OR 돌파 제외한 핵심 3게이트 (백테스트 후보 선정용)."""
        return self.pass_gap and self.pass_rvol and self.pass_adr

    @property
    def all_passed(self) -> bool:
        """OR 돌파까지 포함 (라이브/분봉 entry 확정용)."""
        return self.gates_passed and self.pass_or_break

    @property
    def fail_reasons(self) -> list[str]:
        r: list[str] = []
        if not self.pass_gap:
            r.append(f"gap {self.gap_pct:.1f}% < {EP_GAP_MIN}%")
        if not self.pass_rvol:
            r.append(f"rvol {self.rvol:.1f}x < {EP_RVOL_MIN}x")
        if not self.pass_adr:
            r.append(f"adr {self.adr_pct:.1f}% < {EP_ADR_PCT_MIN}%")
        if not self.pass_or_break:
            r.append("no_or_break")
        return r

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "gap_pct": round(self.gap_pct, 3),
            "rvol": round(self.rvol, 3),
            "adr_pct": round(self.adr_pct, 3),
            "prev_close": round(self.prev_close, 4),
            "open_price": round(self.open_price, 4),
            "or_high": round(self.or_high, 4) if self.or_high is not None else None,
            "pass_gap": self.pass_gap,
            "pass_rvol": self.pass_rvol,
            "pass_adr": self.pass_adr,
            "pass_or_break": self.pass_or_break,
            "has_intraday": self.has_intraday,
            "gates_passed": self.gates_passed,
            "all_passed": self.all_passed,
            "fail_reasons": self.fail_reasons,
        }


def compute_adr_pct(daily_bars: pd.DataFrame, lookback: int = EP_ADR_LOOKBACK) -> float:
    """ADR(N)% = 직전 N일 (high/low - 1) 평균 × 100. ATR과 별개의 단순 일중 변동폭."""
    if daily_bars is None or len(daily_bars) < 2:
        return 0.0
    recent = daily_bars.tail(lookback)
    hl = (recent["high"] / recent["low"] - 1.0) * 100.0
    val = float(hl.mean())
    return val if val == val else 0.0  # NaN guard


def compute_gap_pct(prev_close: float, open_price: float) -> float:
    if prev_close <= 0:
        return 0.0
    return (open_price - prev_close) / prev_close * 100.0


def compute_daily_rvol(today_volume: float, daily_bars: pd.DataFrame, lookback: int = EP_ADR_LOOKBACK) -> float:
    """일봉 RVOL = 당일 거래량 / 직전 N일 평균 거래량 (일봉 백테스트용 근사)."""
    if daily_bars is None or len(daily_bars) < 2 or today_volume <= 0:
        return 0.0
    avg = float(daily_bars["volume"].tail(lookback).mean())
    if avg <= 0:
        return 0.0
    return today_volume / avg


def evaluate_ep(
    symbol: str,
    *,
    prev_close: float,
    open_price: float,
    today_volume: float,
    hist_daily_bars: pd.DataFrame,
    intraday_bars: pd.DataFrame | None = None,
    gap_min: float = EP_GAP_MIN,
    rvol_min: float = EP_RVOL_MIN,
    adr_min: float = EP_ADR_PCT_MIN,
) -> EPEvaluation | None:
    """EP 게이트 평가.

    hist_daily_bars: 당일 제외 직전 일봉 (ADR/RVOL 평균용, ≥ lookback+1 권장).
    intraday_bars  : 당일 1분봉 (있으면 OR 돌파까지 평가, 없으면 일봉 모드).
    데이터 부족 시 None.
    """
    if hist_daily_bars is None or len(hist_daily_bars) < 2 or prev_close <= 0 or open_price <= 0:
        return None

    gap_pct = compute_gap_pct(prev_close, open_price)
    rvol = compute_daily_rvol(today_volume, hist_daily_bars)
    adr_pct = compute_adr_pct(hist_daily_bars)

    pass_gap = gap_pct >= gap_min
    pass_rvol = rvol >= rvol_min
    pass_adr = adr_pct >= adr_min

    or_high = None
    or_break_price = None
    has_intraday = False
    pass_or_break = True  # 분봉 없으면 일봉 모드 — 자동 통과

    if intraday_bars is not None and not intraday_bars.empty:
        has_intraday = True
        idx = intraday_bars.index
        if getattr(idx, "tz", None) is not None:
            et = intraday_bars.set_index(idx.tz_convert("America/New_York"))
        else:
            et = intraday_bars
        mask = (et.index.time >= ET_OPEN_TIME) & (et.index.time < ET_OR_END_TIME)
        or_slice = et[mask]
        if not or_slice.empty:
            or_high = float(or_slice["high"].max())
            after = et[et.index.time >= ET_OR_END_TIME]
            if not after.empty:
                or_break_price = float(after["high"].max())
                pass_or_break = or_break_price > or_high
            else:
                pass_or_break = False
        else:
            pass_or_break = False

    return EPEvaluation(
        symbol=symbol,
        gap_pct=gap_pct,
        rvol=rvol,
        adr_pct=adr_pct,
        prev_close=prev_close,
        open_price=open_price,
        or_high=or_high,
        or_break_price=or_break_price,
        pass_gap=pass_gap,
        pass_rvol=pass_rvol,
        pass_adr=pass_adr,
        pass_or_break=pass_or_break,
        has_intraday=has_intraday,
    )
