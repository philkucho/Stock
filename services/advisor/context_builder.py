"""Claude 자문에 넘길 컨텍스트 dict 구성.

morning_context :
  - v10 picks top-5 (entry/stop/1R/2R + score_breakdown)
  - regime (VIX, SPY/QQQ trend)
  - 현재 포지션 + daily loss 잔여 한도
  - 최근 7일 outcome 통계 (hit rate, avg R)
  - 종목별 최신 뉴스 헤드라인 (24h) — finnhub-python

intraday_context(symbol) :
  - 해당 symbol의 trade_plan 현재 상태 (2-tier fill 진행도)
  - broker 현재 포지션 (avg, unrealized PnL)
  - 최근 가격액션 (1m bar 30개)
  - 신규 뉴스 (마지막 N시간)
  - regime + daily loss 한도 잔여

설계: 백엔드가 *모든* 데이터를 미리 채워서 Claude에 넘긴다 (tool use 안 씀).
Phase 3에서 tool use로 전환 가능하지만 Phase 1~2는 보수적으로 pre-fetched.

⚠️ 시크릿 노출 금지: API_KEY 같은 값은 dict에 절대 넣지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.models import PickOutcome, SystemPickLog, TradePlan

logger = logging.getLogger("advisor.context")


# ──── Morning context ────


async def build_morning_context(session: AsyncSession, target: date) -> dict[str, Any]:
    """장 시작 전 자문 컨텍스트 구성.

    호출 순서:
      1. v10 picks + score breakdown (trading._build_picks 재사용)
      2. market regime
      3. broker 현재 포지션 + 일일 손실 잔여
      4. 최근 7일 outcome 통계
      5. 종목별 24h 뉴스 헤드라인 (best-effort, 실패해도 진행)
    """
    # 1) Picks + brief — trading.py의 캐시된 함수 재사용
    from api.routes.trading import _get_cached_brief_and_picks

    brief, picks = await _get_cached_brief_and_picks(session)

    # 2) Regime은 brief 안에 이미 들어있음 (regime_mode, indices 등)

    # 3) 포지션 + 일일 손실 잔여
    positions_ctx: list[dict[str, Any]] = []
    account_ctx: dict[str, Any] = {}
    try:
        from broker_adapter import get_adapter

        adapter = get_adapter()
        try:
            acc = await adapter.get_account()
            account_ctx = {
                "equity": acc.equity,
                "last_equity": acc.last_equity,
                "buying_power": acc.buying_power,
                "daily_pnl_pct": round(acc.daily_pnl_pct, 2),
                "daily_loss_halt_pct": float(os.environ.get("DAILY_LOSS_HALT_PCT", "-3.0")),
                "daily_loss_close_pct": float(os.environ.get("DAILY_LOSS_CLOSE_PCT", "-5.0")),
                "trading_blocked": acc.trading_blocked,
            }
            positions = await adapter.get_positions()
            positions_ctx = [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_entry": round(p.avg_entry_price, 4),
                    "current": round(p.current_price, 4),
                    "unrealized_pl": round(p.unrealized_pl, 2),
                    "unrealized_pl_pct": round(p.unrealized_pl_pct, 2),
                }
                for p in positions
            ]
        finally:
            await adapter.close()
    except Exception as exc:
        logger.warning("[context] broker fetch failed (계속 진행): %s", exc)
        account_ctx = {"error": str(exc)}

    # 4) 최근 7일 outcome 통계 (integrated 시스템만)
    recent_stats = await _recent_outcome_stats(session, target, lookback_days=7)

    # 5) 뉴스 헤드라인 — best-effort
    news_by_symbol = await _fetch_news_headlines(
        [p.symbol for p in picks],
        hours_back=24,
    )

    # picks를 dict로 변환 (Pydantic → dict)
    picks_ctx: list[dict[str, Any]] = []
    for p in picks:
        picks_ctx.append({
            "rank": p.rank,
            "symbol": p.symbol,
            "sector": p.sector,
            "composite_score": p.composite_score,
            "tier": p.tier,
            "consensus_tier": p.consensus_tier,
            "consensus_systems": p.consensus_systems,
            "system_source": p.system_source,
            "entry": float(p.entry_price),
            "stop": float(p.stop_price),
            "target_1r": float(p.target_1r),
            "target_2r": float(p.target_2r),
            "risk_per_share": float(p.risk_per_share),
            "risk_pct": p.risk_pct,
            "score_breakdown": [
                {"name": b.label_ko, "points": b.points, "kind": b.kind}
                for b in p.score_breakdown
            ],
            "news_24h": news_by_symbol.get(p.symbol, []),
        })

    return {
        "date": target.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "regime": {
            "mode": brief.regime_mode,
            "score": brief.regime_score,
            "signals": brief.regime_signals,
            "indices_pct": brief.indices,
            "summary": brief.summary,
            "position_size_multiplier": brief.position_size_multiplier,
            "long_blocked": brief.long_blocked,
        },
        "account": account_ctx,
        "positions": positions_ctx,
        "recent_outcomes": recent_stats,
        "picks": picks_ctx,
        "constraints": {
            "max_positions": 5,
            "sector_cap": int(os.environ.get("SECTOR_CAP", "2")),
            "rr_min": 1.5,
        },
    }


# ──── Intraday context ────


async def build_intraday_context(
    session: AsyncSession,
    symbol: str,
    target: date,
    trigger_reason: str,
) -> dict[str, Any]:
    """장중 단일 종목 자문 컨텍스트.

    trigger_reason: 'price_spike' | 'price_drop' | 'news' | 'rvol' | 'manual'
    """
    sym = symbol.upper()

    # 해당 symbol의 trade_plan
    stmt = (
        select(TradePlan)
        .options(selectinload(TradePlan.outcomes))
        .where(TradePlan.plan_date == target)
        .where(TradePlan.symbol == sym)
    )
    plan = (await session.execute(stmt)).scalar_one_or_none()
    plan_ctx: dict[str, Any] | None = None
    if plan:
        plan_ctx = {
            "id": plan.id,
            "symbol": plan.symbol,
            "rank": plan.rank,
            "entry": float(plan.entry_price),
            "stop": float(plan.stop_price),
            "target_1r": float(plan.target_1r),
            "target_2r": float(plan.target_2r),
            "shares": plan.shares,
            "filled_qty_1r": plan.filled_qty_1r or 0,
            "filled_qty_2r": plan.filled_qty_2r or 0,
            "entry_filled_qty_1": plan.entry_filled_qty_1 or 0,
            "entry_filled_qty_2": plan.entry_filled_qty_2 or 0,
            "confirm_status": plan.confirm_status,
            "dispatch_mode": plan.dispatch_mode,
            "broker_order_ids": plan.broker_order_ids or [],
            "stop_raised_to_breakeven": bool(
                (plan.score_meta or {}).get("stop_raised_to_breakeven")
            ),
        }

    # 현재 포지션 + 가격 액션
    position_ctx: dict[str, Any] | None = None
    intraday_bars: list[dict[str, Any]] = []
    try:
        from broker_adapter import get_adapter

        adapter = get_adapter()
        try:
            positions = await adapter.get_positions()
            pos = next((p for p in positions if p.symbol == sym), None)
            if pos:
                position_ctx = {
                    "qty": pos.qty,
                    "avg_entry": round(pos.avg_entry_price, 4),
                    "current": round(pos.current_price, 4),
                    "unrealized_pl": round(pos.unrealized_pl, 2),
                    "unrealized_pl_pct": round(pos.unrealized_pl_pct, 2),
                }
        finally:
            await adapter.close()
    except Exception as exc:
        logger.warning("[intraday_ctx] broker fetch failed: %s", exc)

    # 최근 1m 30 bars (yfinance)
    try:
        import yfinance as yf

        df = await asyncio.to_thread(
            yf.download,
            sym,
            period="1d",
            interval="1m",
            progress=False,
            auto_adjust=False,
        )
        if df is not None and not df.empty:
            import pandas as pd

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.lower() for c in df.columns]
            tail = df.tail(30)
            for ts, row in tail.iterrows():
                intraday_bars.append({
                    "t": ts.isoformat(),
                    "o": round(float(row["open"]), 4),
                    "h": round(float(row["high"]), 4),
                    "l": round(float(row["low"]), 4),
                    "c": round(float(row["close"]), 4),
                    "v": int(float(row["volume"])),
                })
    except Exception as exc:
        logger.warning("[intraday_ctx] yfinance fetch failed: %s", exc)

    # 뉴스
    news = (await _fetch_news_headlines([sym], hours_back=6)).get(sym, [])

    # regime
    regime_ctx: dict[str, Any] = {}
    try:
        from scanner.regime import evaluate_regime

        regime = await asyncio.to_thread(evaluate_regime, target)
        regime_ctx = {
            "mode": regime.mode,
            "score": regime.score,
            "long_blocked": regime.long_blocked(),
        }
    except Exception as exc:
        logger.warning("[intraday_ctx] regime failed: %s", exc)

    return {
        "symbol": sym,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "trigger_reason": trigger_reason,
        "trade_plan": plan_ctx,
        "position": position_ctx,
        "intraday_bars_1m": intraday_bars,
        "news_recent": news,
        "regime": regime_ctx,
    }


# ──── Helpers ────


async def _recent_outcome_stats(
    session: AsyncSession, target: date, lookback_days: int = 7
) -> dict[str, Any]:
    """integrated 시스템 picks의 최근 N일 outcome 통계."""
    cutoff = target - timedelta(days=lookback_days)
    stmt = (
        select(PickOutcome, SystemPickLog)
        .join(SystemPickLog, PickOutcome.pick_log_id == SystemPickLog.id)
        .where(SystemPickLog.system_id == "integrated")
        .where(SystemPickLog.pick_date >= cutoff)
        .where(PickOutcome.horizon_days == 5)
    )
    rows = list((await session.execute(stmt)).all())
    if not rows:
        return {"days": lookback_days, "samples": 0}

    pcts = [float(o.pct_return) for o, _ in rows]
    alphas = [float(o.alpha) for o, _ in rows]
    wins = sum(1 for o, _ in rows if o.win_simple)
    return {
        "days": lookback_days,
        "samples": len(rows),
        "win_rate": round(wins / len(rows) * 100, 1),
        "avg_pct_return": round(sum(pcts) / len(pcts), 2),
        "avg_alpha": round(sum(alphas) / len(alphas), 2),
        "horizon_days": 5,
    }


async def _fetch_news_headlines(symbols: list[str], hours_back: int = 24) -> dict[str, list[dict[str, Any]]]:
    """Finnhub-python으로 종목별 뉴스 헤드라인 fetch.

    best-effort: 실패 종목은 빈 list. API 키 없으면 전체 빈 dict.
    """
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key or not symbols:
        return {}

    try:
        import finnhub
    except ImportError:
        return {}

    client = finnhub.Client(api_key=api_key)
    out: dict[str, list[dict[str, Any]]] = {}
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp()
    today_iso = date.today().isoformat()
    from_iso = (date.today() - timedelta(days=2)).isoformat()

    async def _one(sym: str) -> tuple[str, list[dict[str, Any]]]:
        try:
            items = await asyncio.to_thread(client.company_news, sym, from_iso, today_iso)
            headlines = []
            for it in items or []:
                if it.get("datetime", 0) < cutoff_ts:
                    continue
                headlines.append({
                    "headline": it.get("headline", "")[:200],
                    "source": it.get("source"),
                    "url": it.get("url"),
                    "datetime": datetime.fromtimestamp(
                        it.get("datetime", 0), tz=timezone.utc
                    ).isoformat() if it.get("datetime") else None,
                })
                if len(headlines) >= 5:
                    break
            return sym, headlines
        except Exception as exc:
            logger.warning("[news] %s fetch failed: %s", sym, exc)
            return sym, []

    results = await asyncio.gather(*(_one(s) for s in symbols))
    for sym, headlines in results:
        if headlines:
            out[sym] = headlines
    return out


def fetch_current_price(symbol: str) -> float | None:
    """단일 종목 현재가 (validation용). yfinance fast_info."""
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        info = t.fast_info
        price = info.get("last_price") or info.get("regular_market_price")
        return float(price) if price else None
    except Exception as exc:
        logger.warning("[price] %s fetch failed: %s", symbol, exc)
        return None
