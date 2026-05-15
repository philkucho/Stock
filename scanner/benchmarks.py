"""벤치마크 + 섹터 ETF 데이터 fetch + 캐시.

Stage 2 점수 산출 시 RS Rating·섹터 강도·시장 환경 게이트 입력으로 사용.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

import pandas as pd

from backtests.data_cache import get_bars

logger = logging.getLogger(__name__)

# GICS 섹터 → SPDR 섹터 ETF
SECTOR_ETF_MAP: dict[str, str] = {
    "Technology": "XLK",
    "Information Technology": "XLK",
    "Communication Services": "XLC",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Materials": "XLB",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

BENCHMARKS = ("SPY", "QQQ", "IWM")


def all_benchmark_symbols() -> list[str]:
    """벤치마크 + 모든 고유 섹터 ETF."""
    return list(BENCHMARKS) + sorted(set(SECTOR_ETF_MAP.values()))


@lru_cache(maxsize=32)
def get_benchmark_bars(symbol: str, lookback_days: int = 400) -> pd.DataFrame | None:
    """일봉 OHLCV. 실패 시 None (caller가 graceful degrade)."""
    try:
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=lookback_days)).isoformat()
        return get_bars(symbol, start, end, "1d")
    except Exception as exc:
        logger.warning("benchmark fetch failed for %s: %s", symbol, exc)
        return None


def sector_etf_for(sector: str | None) -> str | None:
    if not sector:
        return None
    return SECTOR_ETF_MAP.get(sector)


def market_uptrend_check() -> tuple[bool, dict]:
    """G1 시장 환경: QQQ close > 20EMA. 반환 (passed, diagnostic)."""
    qqq = get_benchmark_bars("QQQ", lookback_days=60)
    if qqq is None or len(qqq) < 21:
        return True, {"reason": "no_data"}  # 데이터 없으면 통과 (graceful)
    ema20 = qqq["close"].ewm(span=20, adjust=False).mean()
    last_close = float(qqq["close"].iloc[-1])
    last_ema = float(ema20.iloc[-1])
    return last_close > last_ema, {
        "qqq_close": round(last_close, 2),
        "qqq_ema20": round(last_ema, 2),
        "above_ema20": last_close > last_ema,
    }
