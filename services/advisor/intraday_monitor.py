"""장중 모니터 — 트리거 감지 후 Claude 자문 호출.

트리거 조건:
  (a) 보유 종목의 1분봉 기준 가격 ±2σ 이탈 (직전 30바 std 대비)
  (b) Finnhub 신규 뉴스 (마지막 15분 안에 발행된 헤드라인)
  (c) RVOL spike (직전 5분 거래량 > 평소의 2.0×)

dedupe: 같은 symbol에 대해 30분 안에 추천이 이미 있으면 skip.
confidence < ADVISOR_INTRADAY_MIN_CONFIDENCE면 Telegram 알림 안 보냄 (DB만).

호출: scripts/daily_pipeline.py --phase intraday-loop  (cron 매 15분 09:50~15:55)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import TradePlan
from services.advisor.dedupe import has_recent_intraday

logger = logging.getLogger("advisor.intraday")


PRICE_SIGMA_THRESHOLD = 2.0
RVOL_SPIKE_THRESHOLD = 2.0
NEWS_WINDOW_MIN = 15
DEDUPE_WINDOW_MIN = 30


async def run_intraday_loop_iteration(
    session: AsyncSession, target: date, *, force_check: bool = False
) -> dict[str, Any]:
    """1회 실행. cron이 매 15분 호출.

    수행:
      1. 오늘의 trade_plan(broker_order_ids 발송된 것) + 현재 broker 포지션 = 모니터 대상 set
      2. 각 종목에 대해 트리거 조건 평가
      3. 충족 시 dedupe 체크 후 run_intraday_check 호출

    force_check=True (매 시간 정기 검토 cron 용): 트리거/dedupe 평가 skip, monitor set 전종목에 대해
    "hourly_check" 트리거로 LLM 호출. 비용 발생.
    """
    out: dict[str, Any] = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "advisor_enabled": os.environ.get("ADVISOR_ENABLED", "false").strip().lower() == "true",
        "force_check": force_check,
        "symbols_checked": [],
        "triggered": [],
        "skipped_dedupe": [],
        "errors": [],
    }
    if not out["advisor_enabled"]:
        out["status"] = "disabled"
        return out

    symbols = await _build_monitor_set(session, target)
    out["monitor_set"] = sorted(symbols)

    for sym in symbols:
        if force_check:
            triggers = ["hourly_check"]
        else:
            try:
                triggers = await _evaluate_triggers(sym)
            except Exception as exc:
                logger.warning("[intraday] %s evaluate failed: %s", sym, exc)
                out["errors"].append({"symbol": sym, "stage": "trigger", "error": str(exc)})
                continue

        out["symbols_checked"].append({"symbol": sym, "triggers": triggers})
        if not triggers:
            continue

        if not force_check and await has_recent_intraday(session, sym, window_minutes=DEDUPE_WINDOW_MIN):
            out["skipped_dedupe"].append(sym)
            continue

        primary_trigger = triggers[0]
        try:
            from services.advisor.service import run_intraday_check

            result = await run_intraday_check(session, sym, target, primary_trigger)
            out["triggered"].append({
                "symbol": sym,
                "trigger": primary_trigger,
                "all_triggers": triggers,
                "status": result.get("status"),
                "action": result.get("action"),
                "confidence": result.get("confidence"),
                "recommendation_id": result.get("recommendation_id"),
            })
        except Exception as exc:
            logger.exception("[intraday] %s check failed", sym)
            out["errors"].append({"symbol": sym, "stage": "check", "error": str(exc)})

    out["status"] = "ok"
    return out


async def _build_monitor_set(session: AsyncSession, target: date) -> set[str]:
    """오늘 발송된 trade_plan + 현재 broker 포지션 = 모니터 대상.

    watchlist에 있지만 미발송인 종목은 skip — 진입 안 했으므로 add/trim/exit 의미 없음.
    intraday_entry (신규 진입) 트리거는 별도 로직(Phase 2.1+) — 지금은 보유 종목만.
    """
    symbols: set[str] = set()

    stmt = (
        select(TradePlan.symbol)
        .where(TradePlan.plan_date == target)
        .where(TradePlan.broker_order_ids.is_not(None))
    )
    for sym in (await session.execute(stmt)).scalars().all():
        symbols.add((sym or "").upper())

    try:
        from broker_adapter import get_adapter

        adapter = get_adapter()
        try:
            positions = await adapter.get_positions()
            for p in positions:
                symbols.add(p.symbol.upper())
        finally:
            await adapter.close()
    except Exception as exc:
        logger.warning("[intraday] position fetch failed: %s", exc)

    symbols.discard("")
    return symbols


async def _evaluate_triggers(symbol: str) -> list[str]:
    """트리거 조건 평가. 충족된 trigger 이름의 list 반환 (우선순위 순)."""
    triggers: list[str] = []

    # (a) + (c): 1분봉 fetch — std + rvol 한 번에
    bars = await _fetch_recent_1m_bars(symbol)
    if bars is not None and len(bars) >= 6:
        last = bars[-1]
        prior = bars[:-1]

        # (a) Price ±2σ 이탈
        closes = [b["c"] for b in prior]
        if len(closes) >= 5:
            mean = sum(closes) / len(closes)
            var = sum((c - mean) ** 2 for c in closes) / len(closes)
            std = var**0.5
            if std > 0:
                deviation = (last["c"] - mean) / std
                if abs(deviation) >= PRICE_SIGMA_THRESHOLD:
                    triggers.append("price_spike" if deviation > 0 else "price_drop")

        # (c) RVOL spike
        vols = [b["v"] for b in prior if b["v"] > 0]
        if vols:
            avg_vol = sum(vols) / len(vols)
            if avg_vol > 0 and last["v"] / avg_vol >= RVOL_SPIKE_THRESHOLD:
                triggers.append("rvol")

    # (b) 신규 뉴스 (best-effort, 실패해도 진행)
    if await _has_fresh_news(symbol):
        triggers.append("news")

    return triggers


async def _fetch_recent_1m_bars(symbol: str) -> list[dict[str, Any]] | None:
    """yfinance 1m bar 마지막 30개. asyncio.to_thread."""
    try:
        import yfinance as yf

        df = await asyncio.to_thread(
            yf.download,
            symbol,
            period="1d",
            interval="1m",
            progress=False,
            auto_adjust=False,
        )
        if df is None or df.empty:
            return None
        import pandas as pd

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        tail = df.tail(30)
        out: list[dict[str, Any]] = []
        for _ts, row in tail.iterrows():
            out.append({
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"]),
                "v": float(row["volume"]),
            })
        return out
    except Exception as exc:
        logger.warning("[intraday] bar fetch %s failed: %s", symbol, exc)
        return None


async def _has_fresh_news(symbol: str) -> bool:
    """Finnhub 종목 뉴스 — 마지막 15분 안에 새 헤드라인이 있는지."""
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return False
    try:
        import finnhub

        client = finnhub.Client(api_key=api_key)
        today_iso = date.today().isoformat()
        items = await asyncio.to_thread(client.company_news, symbol, today_iso, today_iso)
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=NEWS_WINDOW_MIN)).timestamp()
        return any((it.get("datetime") or 0) >= cutoff for it in (items or []))
    except Exception as exc:
        logger.warning("[intraday] news %s failed: %s", symbol, exc)
        return False
