"""Earnings 캘린더 — 가장 신뢰도 높은 카탈리스트 소스 (확정 일정).

Nasdaq.com earnings calendar 또는 yfinance Ticker.calendar 사용.
유료 API 없이 구현하기 위해 yfinance를 1차 소스로, 필요 시 Nasdaq 스크랩으로 확장.

캐시 정책: 종목별 next earnings date를 매일 1회만 조회 (yfinance가 throttle하므로).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

import yfinance as yf

from scanner.catalysts.types import CatalystHit, CatalystKind

logger = logging.getLogger(__name__)


@lru_cache(maxsize=512)
def _next_earnings_date(symbol: str) -> date | None:
    """yfinance Ticker.calendar에서 다음 실적 발표일 조회. 실패 시 None."""
    try:
        cal = yf.Ticker(symbol).calendar
    except Exception as exc:
        logger.debug("yfinance.calendar failed for %s: %s", symbol, exc)
        return None

    if cal is None:
        return None
    # yfinance returns dict with key 'Earnings Date' (list of datetime/date)
    if isinstance(cal, dict):
        earnings_dates = cal.get("Earnings Date") or cal.get("earnings_date")
        if not earnings_dates:
            return None
        first = earnings_dates[0] if isinstance(earnings_dates, list) else earnings_dates
        if hasattr(first, "date"):
            return first.date()
        if isinstance(first, date):
            return first
    # legacy: DataFrame with 'Earnings Date' column
    try:
        ed = cal.loc["Earnings Date"].iloc[0]
        if hasattr(ed, "date"):
            return ed.date()
    except Exception:
        pass
    return None


def fetch(symbol: str, target_date: date) -> CatalystHit | None:
    """target_date가 다음 실적 발표일과 같거나 직전(D-1) 인지 확인.

    ER 당일은 단타 진입 금지(G6 함정 회피)지만 D-1은 카탈리스트로 인정.
    G6 게이트가 별도로 ER 당일을 제외하므로 여기서는 ±1일까지 점수 부여.
    """
    er = _next_earnings_date(symbol)
    if er is None:
        return None
    delta = (er - target_date).days
    if -1 <= delta <= 1:
        when = (
            "today"
            if delta == 0
            else ("tomorrow" if delta == 1 else "yesterday")
        )
        return CatalystHit(
            kind=CatalystKind.EARNINGS,
            headline=f"Earnings {when} ({er.isoformat()})",
            source="yfinance.calendar",
        )
    return None


def is_er_day(symbol: str, target_date: date) -> bool:
    """G6 게이트용 — 정확히 target_date가 ER 당일인지."""
    er = _next_earnings_date(symbol)
    return er is not None and er == target_date
