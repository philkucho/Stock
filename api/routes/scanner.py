"""Scanner 엔드포인트 — scan_momentum 파이프라인을 web UI에 노출.

GET /api/scanner/today        : 오늘 후보 + regime + earnings phase 분류
GET /api/scanner/regime       : 매크로 regime 상태 (SPY MA200 + VIX 25)
GET /api/scanner/whitelist    : 현재 활성 WHITELIST (v3 default) + sector 분포
GET /api/scanner/diagnostics  : universe 크기, 최신 일자, sector 분포 등
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from api.db import get_session
from api.db.models import Bar
from scripts.scan_momentum import (
    EARNINGS_CALENDAR_PATH,
    MACRO_SKIP,
    earnings_phase,
    evaluate_at_date,
    fetch_bars,
    list_symbols,
    load_earnings_calendar,
)
from signals.macro_regime import (
    compute_regime_state,
    is_regime_on,
    load_macro_bars,
    SPY_MA_PERIOD,
    VIX_THRESHOLD,
)

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FILTER_PATH = PROJECT_ROOT / "data" / "symbol_filter_v3_sp500.json"
SECTOR_MAP_PATH = PROJECT_ROOT / "data" / "sector_map.json"


def _load_filter(path: Path) -> dict[str, dict] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for group in ("whitelist", "blacklist", "unknown"):
        for r in payload.get(group, []):
            out[r["symbol"]] = {**r, "group": group}
    return out


def _load_sectors() -> dict[str, str]:
    if not SECTOR_MAP_PATH.exists():
        return {}
    payload = json.loads(SECTOR_MAP_PATH.read_text(encoding="utf-8"))
    return {sym: v.get("sector", "Unknown") for sym, v in payload["mapping"].items()}


class ScannerCandidate(BaseModel):
    rank: int
    symbol: str
    sector: str | None
    group: str | None  # whitelist | blacklist | unknown
    earnings_phase: str  # pre | post | clean
    earnings_next: str | None
    earnings_days: int | None
    as_of: str
    close: float
    volume: int
    vol_vs_20d_avg: float | None
    signals: dict[str, int]
    volume_score: int
    momentum_score: int
    total_score: int
    historical: dict[str, Any] | None  # {n, hit_rate, avg_ret, median_ret}


class RegimeStatus(BaseModel):
    on: bool | None
    spy_close: float | None
    spy_ma200: float | None
    spy_above_ma: bool | None
    vix_close: float | None
    vix_threshold: float
    last_update: str | None


class TodayScanResponse(BaseModel):
    as_of: str
    regime: RegimeStatus
    n_candidates: int
    n_pre_blackout: int
    n_post_pead: int
    n_clean: int
    candidates: list[ScannerCandidate]


@router.get("/today", response_model=TodayScanResponse)
async def get_today(
    target_date: str | None = Query(None, description="YYYY-MM-DD, default 최신"),
    score_min: int = Query(1, ge=0, le=10),
    earnings_mode: str = Query("pre_only", pattern="^(off|annotate|exclude|pre_only)$"),
    earnings_days: int = Query(5, ge=1, le=10),
    filter_mode: str = Query(
        "whitelist-only", pattern="^(whitelist-only|no-blacklist|annotate|all)$"
    ),
    filter_path: str | None = Query(None, description="symbol_filter JSON 경로 override"),
    top: int = Query(50, ge=1, le=200),
) -> TodayScanResponse:
    """오늘 후보 스캔. scan_momentum CLI와 동일 결과를 JSON으로."""
    target = (
        datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else None
    )

    fpath = Path(filter_path) if filter_path else DEFAULT_FILTER_PATH
    filter_data = _load_filter(fpath)
    sectors = _load_sectors()

    earnings_data = (
        load_earnings_calendar(EARNINGS_CALENDAR_PATH) if earnings_mode != "off" else None
    )

    # 매크로 regime
    macro = await load_macro_bars()
    state = compute_regime_state(macro, fallback_when_missing=False)
    regime_status = _build_regime_status(macro, state, target)

    # 종목별 평가
    symbols = await list_symbols()
    candidates: list[dict] = []
    for sym in symbols:
        bars = await fetch_bars(sym)
        result = evaluate_at_date(bars, target)
        if result is None:
            continue
        result["symbol"] = sym
        result["sector"] = sectors.get(sym)
        candidates.append(result)
    candidates.sort(
        key=lambda r: (r["total_score"], r["volume_score"], r["momentum_score"]),
        reverse=True,
    )
    candidates = [r for r in candidates if r["total_score"] >= score_min]

    # earnings 분류
    n_pre = n_post = n_clean = 0
    for r in candidates:
        if earnings_data is None:
            r["earnings_phase"] = "clean"
            r["earnings_next"] = None
            r["earnings_days"] = None
        else:
            phase = earnings_phase(
                r["symbol"], date.fromisoformat(r["as_of"]), earnings_data, earnings_days
            )
            r["earnings_phase"] = phase
            sym_data = earnings_data.get(r["symbol"], {})
            nxt = sym_data.get("next")
            if nxt:
                r["earnings_next"] = nxt
                try:
                    nd = date.fromisoformat(nxt)
                    r["earnings_days"] = (nd - date.fromisoformat(r["as_of"])).days
                except Exception:
                    r["earnings_days"] = None
            else:
                r["earnings_next"] = None
                r["earnings_days"] = None
        if r["earnings_phase"] == "pre":
            n_pre += 1
        elif r["earnings_phase"] == "post":
            n_post += 1
        else:
            n_clean += 1

    # filter mode 적용
    if filter_data is not None:
        if filter_mode == "whitelist-only":
            candidates = [
                r
                for r in candidates
                if filter_data.get(r["symbol"], {}).get("group") == "whitelist"
            ]
        elif filter_mode == "no-blacklist":
            candidates = [
                r
                for r in candidates
                if filter_data.get(r["symbol"], {}).get("group") != "blacklist"
            ]

    # earnings mode 적용
    if earnings_data is not None:
        if earnings_mode == "exclude":
            candidates = [r for r in candidates if r["earnings_phase"] == "clean"]
        elif earnings_mode == "pre_only":
            candidates = [r for r in candidates if r["earnings_phase"] != "pre"]

    # top 자르기
    candidates = candidates[:top]

    # group/historical 채우기
    out_list: list[ScannerCandidate] = []
    for i, r in enumerate(candidates, 1):
        sym = r["symbol"]
        f_entry = filter_data.get(sym) if filter_data else None
        hist = None
        group = None
        if f_entry:
            group = f_entry["group"]
            hist = {
                k: f_entry[k]
                for k in ("n", "hit_rate", "avg_ret", "median_ret")
                if k in f_entry
            }
        out_list.append(
            ScannerCandidate(
                rank=i,
                symbol=sym,
                sector=r.get("sector"),
                group=group,
                earnings_phase=r["earnings_phase"],
                earnings_next=r.get("earnings_next"),
                earnings_days=r.get("earnings_days"),
                as_of=r["as_of"],
                close=r["close"],
                volume=r["volume"],
                vol_vs_20d_avg=r.get("vol_vs_20d_avg"),
                signals=r["signals"],
                volume_score=r["volume_score"],
                momentum_score=r["momentum_score"],
                total_score=r["total_score"],
                historical=hist,
            )
        )

    as_of_str = candidates[0]["as_of"] if candidates else (
        target.isoformat() if target else date.today().isoformat()
    )
    return TodayScanResponse(
        as_of=as_of_str,
        regime=regime_status,
        n_candidates=len(out_list),
        n_pre_blackout=n_pre,
        n_post_pead=n_post,
        n_clean=n_clean,
        candidates=out_list,
    )


def _build_regime_status(
    macro: dict[str, pd.DataFrame], state: pd.Series, target: date | None
) -> RegimeStatus:
    spy = macro.get("SPY")
    vix = macro.get("VIX")
    if spy is None or vix is None or state.empty:
        return RegimeStatus(
            on=None, spy_close=None, spy_ma200=None, spy_above_ma=None,
            vix_close=None, vix_threshold=VIX_THRESHOLD, last_update=None,
        )

    if target:
        target_ts = pd.Timestamp(target, tz="UTC")
        spy_filt = spy[spy.index <= target_ts + pd.Timedelta(days=1)]
        vix_filt = vix[vix.index <= target_ts + pd.Timedelta(days=1)]
    else:
        spy_filt, vix_filt = spy, vix

    if spy_filt.empty or vix_filt.empty:
        return RegimeStatus(
            on=None, spy_close=None, spy_ma200=None, spy_above_ma=None,
            vix_close=None, vix_threshold=VIX_THRESHOLD, last_update=None,
        )

    spy_close = float(spy_filt["close"].iloc[-1])
    spy_ma = float(spy_filt["close"].rolling(SPY_MA_PERIOD).mean().iloc[-1])
    spy_above = spy_close > spy_ma
    vix_close = float(vix_filt["close"].iloc[-1])
    last_date = spy_filt.index[-1].date().isoformat()

    on = is_regime_on(state, target if target else spy_filt.index[-1].date())
    return RegimeStatus(
        on=on,
        spy_close=spy_close,
        spy_ma200=spy_ma,
        spy_above_ma=spy_above,
        vix_close=vix_close,
        vix_threshold=VIX_THRESHOLD,
        last_update=last_date,
    )


@router.get("/regime")
async def get_regime(target_date: str | None = Query(None)) -> RegimeStatus:
    """현재 (또는 지정 날짜) 매크로 regime 상태."""
    target = datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else None
    macro = await load_macro_bars()
    state = compute_regime_state(macro, fallback_when_missing=False)
    return _build_regime_status(macro, state, target)


class WhitelistResponse(BaseModel):
    n_whitelist: int
    n_blacklist: int
    n_unknown: int
    config: dict[str, Any]
    by_sector: dict[str, list[str]]
    whitelist: list[dict[str, Any]]


@router.get("/whitelist", response_model=WhitelistResponse)
async def get_whitelist(
    filter_path: str | None = Query(None),
) -> WhitelistResponse:
    """현재 활성 WHITELIST + sector 분포."""
    fpath = Path(filter_path) if filter_path else DEFAULT_FILTER_PATH
    if not fpath.exists():
        raise HTTPException(404, f"Filter not found: {fpath}")
    payload = json.loads(fpath.read_text(encoding="utf-8"))
    sectors = _load_sectors()

    wl = payload.get("whitelist", [])
    by_sector: dict[str, list[str]] = {}
    for r in wl:
        sym = r["symbol"]
        sec = sectors.get(sym, "Unknown")
        by_sector.setdefault(sec, []).append(sym)
    for sec in by_sector:
        by_sector[sec].sort()

    return WhitelistResponse(
        n_whitelist=len(wl),
        n_blacklist=len(payload.get("blacklist", [])),
        n_unknown=len(payload.get("unknown", [])),
        config=payload.get("config", {}),
        by_sector=by_sector,
        whitelist=wl,
    )


@router.get("/diagnostics")
async def get_diagnostics(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """DB universe 상태."""
    n_sym = await session.scalar(
        select(func.count(func.distinct(Bar.symbol))).where(Bar.interval == "1d")
    )
    n_rows = await session.scalar(
        select(func.count()).where(Bar.interval == "1d")
    )
    latest = await session.scalar(
        select(func.max(Bar.time)).where(Bar.interval == "1d")
    )
    earliest = await session.scalar(
        select(func.min(Bar.time)).where(Bar.interval == "1d")
    )
    n_1m_sym = await session.scalar(
        select(func.count(func.distinct(Bar.symbol))).where(Bar.interval == "1m")
    )
    n_1m_rows = await session.scalar(
        select(func.count()).where(Bar.interval == "1m")
    )
    earnings = load_earnings_calendar(EARNINGS_CALENDAR_PATH)
    return {
        "interval_1d": {
            "n_symbols": n_sym,
            "n_rows": n_rows,
            "earliest": earliest.isoformat() if earliest else None,
            "latest": latest.isoformat() if latest else None,
        },
        "interval_1m": {
            "n_symbols": n_1m_sym,
            "n_rows": n_1m_rows,
        },
        "earnings_calendar": {
            "path": str(EARNINGS_CALENDAR_PATH.relative_to(PROJECT_ROOT)),
            "n_symbols": len(earnings) if earnings else 0,
        },
        "filter_path": str(DEFAULT_FILTER_PATH.relative_to(PROJECT_ROOT)),
    }
