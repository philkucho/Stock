"""yfinance 데이터 로컬 캐시 + 종목 풀 관리.

캐시는 `data/cache/{symbol}_{interval}.parquet`. 처음 한 번 wide window
(2010-01-01 ~ 오늘)로 다운로드하고, 이후엔 디스크에서 즉시 로드.
강제 갱신은 `refresh=True` 또는 `refresh_cache(symbol)`.

종목 풀:
- "default": data/sp500_tickers.json 의 50개 메가캡
- "sp500":   Wikipedia 스크랩 (500개 전체) — 처음 1회 fetch, 캐시
- 단일 심볼: 그대로 사용
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache"
TICKERS_DIR = REPO_ROOT / "data"

DEFAULT_START = "2010-01-01"


def _normalize_yf_df(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance 응답을 표준 OHLCV DataFrame으로 변환 (UTC tz, 소문자 컬럼)."""
    if df is None or df.empty:
        raise ValueError("Empty yfinance response")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df


def get_bars(
    symbol: str,
    start: str,
    end: str,
    interval: str = "1d",
    refresh: bool = False,
) -> pd.DataFrame:
    """단일 종목 OHLCV 반환. 캐시 우선, 없으면 yfinance 다운로드."""
    cache_path = CACHE_DIR / f"{symbol}_{interval}.parquet"

    if not refresh and cache_path.exists():
        df = pd.read_parquet(cache_path)
    else:
        # yfinance `end`는 exclusive — 오늘 bar까지 포함시키려면 +1일
        raw = yf.download(
            symbol,
            start=DEFAULT_START,
            end=(date.today() + timedelta(days=1)).isoformat(),
            interval=interval,
            progress=False,
            auto_adjust=False,
        )
        df = _normalize_yf_df(raw)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path)

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    return df.loc[start_ts:end_ts]


def refresh_cache(symbols: list[str], interval: str = "1d") -> dict[str, str]:
    """여러 종목 일괄 갱신. 결과: {symbol: 'ok'|'error: ...'}"""
    results: dict[str, str] = {}
    for s in symbols:
        try:
            get_bars(s, DEFAULT_START, date.today().isoformat(), interval, refresh=True)
            results[s] = "ok"
        except Exception as exc:
            results[s] = f"error: {exc.__class__.__name__}: {exc}"
    return results


def get_pool(name: str) -> list[str]:
    """종목 풀 리스트.

    'default' / 'sp500' / 'all' / 단일 심볼 / 콤마 구분 ('AAPL,MSFT,NVDA').
    """
    # 콤마 포함되면 inline 리스트로 해석
    if "," in name:
        return [s.strip().upper() for s in name.split(",") if s.strip()]
    name_l = name.lower()
    if name_l in ("default", "small"):
        with open(TICKERS_DIR / "sp500_tickers.json", encoding="utf-8") as f:
            return list(json.load(f)["tickers"])
    if name_l in ("sp500", "all"):
        return _fetch_sp500_full()
    return [name.upper()]


def _fetch_sp500_full() -> list[str]:
    """Wikipedia에서 S&P 500 전체 티커 1회 스크랩, 결과 캐시."""
    import io

    import httpx

    cache_path = TICKERS_DIR / "sp500_full.json"
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return list(json.load(f)["tickers"])

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    # Wikimedia rejects generic UA — needs a descriptive UA per their policy.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    # yfinance 형식: 점(.) → 대시(-)  (예: BRK.B → BRK-B)
    tickers = sorted(df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist())
    cache_path.write_text(
        json.dumps({"tickers": tickers, "fetched_at": datetime.now().isoformat()}, indent=2),
        encoding="utf-8",
    )
    return tickers
