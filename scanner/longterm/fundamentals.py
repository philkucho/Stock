"""중장기 v2 — yfinance 펀더멘털 fetcher + parquet 캐시.

월 1회 503 종목 fetch (`data/cache/fundamentals_{symbol}.parquet`).
누락 종목은 가용 게이트만 평가하고 skip하지 않음 — fail-soft.

수집 필드 (yfinance Ticker().info 기반):
  - revenue_yoy  : TTM 매출 YoY 성장 (info.revenueGrowth)
  - eps_yoy      : TTM EPS YoY (info.earningsGrowth)
  - revenue_qoq  : 최근 분기 매출 YoY (info.revenueQuarterlyGrowth 또는 income_stmt 계산)
  - eps_qoq      : 최근 분기 EPS YoY (info.earningsQuarterlyGrowth)
  - forward_pe   : info.forwardPE
  - trailing_pe  : info.trailingPE
  - peg_ratio    : info.pegRatio
  - profit_margin: info.profitMargins
  - operating_margin: info.operatingMargins
  - roe          : info.returnOnEquity
  - fcf          : info.freeCashflow
  - total_revenue: info.totalRevenue (FCF margin 계산용)
  - fetched_at   : 캐시 타임스탬프

CLI:
    venv/Scripts/python.exe -m scanner.longterm.fundamentals --refresh
    venv/Scripts/python.exe -m scanner.longterm.fundamentals --symbol AAPL
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache" / "fundamentals"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_TTL_DAYS = 30  # 월 1회 갱신
FIELDS = [
    "revenueGrowth",
    "earningsGrowth",
    "revenueQuarterlyGrowth",
    "earningsQuarterlyGrowth",
    "forwardPE",
    "trailingPE",
    "pegRatio",
    "profitMargins",
    "operatingMargins",
    "returnOnEquity",
    "freeCashflow",
    "totalRevenue",
    "marketCap",
]

logger = logging.getLogger(__name__)


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.upper()}.json"


def _is_fresh(path: Path, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    if not path.exists():
        return False
    try:
        with open(path) as f:
            data = json.load(f)
        fetched = datetime.fromisoformat(data.get("fetched_at", "1970-01-01"))
        return (datetime.now() - fetched).days < ttl_days
    except Exception:
        return False


def fetch_one(symbol: str, *, refresh: bool = False) -> dict | None:
    """단일 종목 펀더 — 캐시 우선, 없거나 stale이면 yfinance fetch.

    Returns: dict with normalized field names (snake_case) or None on fail.
    """
    path = _cache_path(symbol)
    if not refresh and _is_fresh(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception as exc:
        logger.debug("fundamentals fetch fail %s: %s", symbol, exc)
        return None

    if not info or info.get("trailingPE") is None and info.get("forwardPE") is None:
        # 빈 응답 — likely delisted or rate-limited
        return None

    rev = info.get("totalRevenue")
    fcf = info.get("freeCashflow")
    fcf_margin = (fcf / rev) if (fcf and rev and rev > 0) else None

    record = {
        "symbol": symbol.upper(),
        "fetched_at": datetime.now().isoformat(),
        "revenue_yoy": info.get("revenueGrowth"),
        "eps_yoy": info.get("earningsGrowth"),
        "revenue_qoq": info.get("revenueQuarterlyGrowth"),
        "eps_qoq": info.get("earningsQuarterlyGrowth"),
        "forward_pe": info.get("forwardPE"),
        "trailing_pe": info.get("trailingPE"),
        "peg_ratio": info.get("pegRatio"),
        "profit_margin": info.get("profitMargins"),
        "operating_margin": info.get("operatingMargins"),
        "roe": info.get("returnOnEquity"),
        "fcf": fcf,
        "total_revenue": rev,
        "fcf_margin": fcf_margin,
        "market_cap": info.get("marketCap"),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return record


def fetch_universe(symbols: list[str], *, refresh: bool = False) -> dict[str, dict]:
    """Universe 일괄 fetch. 캐시 hit는 즉시 반환, miss는 yfinance."""
    out: dict[str, dict] = {}
    miss_count = 0
    for i, sym in enumerate(symbols):
        r = fetch_one(sym, refresh=refresh)
        if r is not None:
            out[sym.upper()] = r
        else:
            miss_count += 1
        if (i + 1) % 50 == 0:
            logger.info("  fundamentals: %d/%d (missing %d)",
                        i + 1, len(symbols), miss_count)
    return out


def load_cached_universe() -> dict[str, dict]:
    """디스크 캐시 전체 로드 (fetch 없이)."""
    out: dict[str, dict] = {}
    for p in CACHE_DIR.glob("*.json"):
        try:
            with open(p) as f:
                d = json.load(f)
            out[d["symbol"]] = d
        except Exception:
            continue
    return out


# ─────── Detail (lazy fetch) — 상세 페이지용 풀 시리즈 + 추가 info ───────

DETAIL_CACHE_DIR = REPO_ROOT / "data" / "cache" / "fundamentals_detail"
DETAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
DETAIL_TTL_DAYS = 30


def _detail_cache_path(symbol: str) -> Path:
    return DETAIL_CACHE_DIR / f"{symbol.upper()}.json"


def _series_from_df(df, row: str, n: int = 8) -> list[dict] | None:
    """yfinance financials DataFrame에서 row 추출 → [{date, value}, ...] (최신순)."""
    if df is None or df.empty or row not in df.index:
        return None
    cols = list(df.columns)[:n]
    out = []
    for c in cols:
        v = df.loc[row, c]
        if v is None or (isinstance(v, float) and (v != v)):  # NaN
            out.append({"date": str(c)[:10], "value": None})
        else:
            try:
                out.append({"date": str(c)[:10], "value": float(v)})
            except (ValueError, TypeError):
                out.append({"date": str(c)[:10], "value": None})
    return out


def fetch_one_detail(symbol: str, *, refresh: bool = False) -> dict | None:
    """단일 종목 상세 — 분기/연간 시리즈 + 추가 info. 30일 TTL 캐시.

    Returns: dict with quarterly/annual series + valuation + risk metrics.
    """
    path = _detail_cache_path(symbol)
    if not refresh and _is_fresh(path, ttl_days=DETAIL_TTL_DAYS):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        if not info:
            return None

        qis = ticker.quarterly_income_stmt  # 분기 손익
        ais = ticker.income_stmt             # 연간 손익
        qbs = ticker.quarterly_balance_sheet  # 분기 재무상태
        qcf = ticker.quarterly_cashflow       # 분기 현금흐름
        try:
            ed = ticker.earnings_dates  # 다가오는/과거 어닝 일정
        except Exception:
            ed = None
    except Exception as exc:
        logger.debug("detail fetch fail %s: %s", symbol, exc)
        return None

    # 다음 어닝
    next_earnings = None
    try:
        if ed is not None and not ed.empty:
            now = pd.Timestamp.now(tz=ed.index.tz)
            fut = [d for d in ed.index if d > now]
            if fut:
                next_earnings = str(fut[-1])[:10]
    except Exception:
        pass

    record = {
        "symbol": symbol.upper(),
        "fetched_at": datetime.now().isoformat(),
        # ── 추가 info (점수 v2엔 미포함, detail UI용) ──
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        "beta": info.get("beta"),
        "ev_ebitda": info.get("enterpriseToEbitda"),
        "ev_revenue": info.get("enterpriseToRevenue"),
        "price_to_book": info.get("priceToBook"),
        "price_to_sales": info.get("priceToSalesTrailing12Months"),
        "institutional_pct": info.get("heldPercentInstitutions"),
        "insider_pct": info.get("heldPercentInsiders"),
        "short_ratio": info.get("shortRatio"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "next_earnings_date": next_earnings,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "long_business_summary": (info.get("longBusinessSummary") or "")[:600],
        # ── 분기 시리즈 (4분기, 최신순) ──
        "quarterly_revenue": _series_from_df(qis, "Total Revenue", 8),
        "quarterly_gross_profit": _series_from_df(qis, "Gross Profit", 8),
        "quarterly_operating_income": _series_from_df(qis, "Operating Income", 8),
        "quarterly_net_income": _series_from_df(qis, "Net Income", 8),
        "quarterly_eps": _series_from_df(qis, "Diluted EPS", 8),
        # ── 연간 시리즈 (4년) ──
        "annual_revenue": _series_from_df(ais, "Total Revenue", 5),
        "annual_net_income": _series_from_df(ais, "Net Income", 5),
        "annual_eps": _series_from_df(ais, "Diluted EPS", 5),
        # ── 현금흐름 (4분기) ──
        "quarterly_ocf": _series_from_df(qcf, "Operating Cash Flow", 8),
        "quarterly_fcf": _series_from_df(qcf, "Free Cash Flow", 8),
        "quarterly_capex": _series_from_df(qcf, "Capital Expenditure", 8),
        # ── 재무상태 (4분기) ──
        "quarterly_total_debt": _series_from_df(qbs, "Total Debt", 8),
        "quarterly_equity": _series_from_df(qbs, "Stockholders Equity", 8),
        "quarterly_current_assets": _series_from_df(qbs, "Current Assets", 8),
        "quarterly_current_liab": _series_from_df(qbs, "Current Liabilities", 8),
        "quarterly_cash": _series_from_df(qbs, "Cash And Cash Equivalents", 8),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return record


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--universe", action="store_true",
                    help="S&P 500 전체 fetch")
    args = ap.parse_args()

    if args.symbol:
        r = fetch_one(args.symbol, refresh=args.refresh)
        print(json.dumps(r, indent=2))
    elif args.universe:
        with open(REPO_ROOT / "data" / "sp500_full.json") as f:
            syms = json.load(f)["tickers"]
        out = fetch_universe(syms, refresh=args.refresh)
        print(f"OK universe: {len(out)} / {len(syms)} fundamentals cached")
