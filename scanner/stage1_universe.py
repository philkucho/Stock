"""Stage 1 Universe — 단타 후보 풀 (월 1회 갱신, ~30종목, 30일 TTL).

입력:
  1) data/whitelist_score5.json — 사용자 큐레이션 (반도체장비/모멘텀/growth_smid)
  2) 일봉 모멘텀 스캐너 — S&P500 + WHITELIST에 near_52w_high, tight_base 평가

제외:
  - data/blacklist_megacap.json (시총 > $500B)
  - 가격 < $5 (페니주)
  - 평균 30일 일거래대금 < $20M

출력:
  - DB universe_members 테이블 upsert (source 별로 valid_until 갱신)
  - 콘솔 보고서

CLI:
    python -m scanner.stage1_universe --refresh
    python -m scanner.stage1_universe --revalidate-only
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import UniverseMember
from backtests.data_cache import get_bars, get_pool
from signals import SIGNAL_REGISTRY

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
WHITELIST_PATH = REPO_ROOT / "data" / "whitelist_score5.json"
BLACKLIST_PATH = REPO_ROOT / "data" / "blacklist_megacap.json"

TTL_DAYS = 30
MIN_PRICE = 5.0
MIN_AVG_DOLLAR_VOL_30D = 20_000_000
NEAR_52W_HIGH_PCT = 0.10  # 일봉 모멘텀 스캐너용 (Score 5 1차 필터보다 약간 느슨)


@dataclass
class CandidateRow:
    symbol: str
    source: str
    category: str | None
    base_score: float
    extra: dict
    notes: str | None = None


# ---------- 입력 로더 ----------


def load_whitelist() -> list[CandidateRow]:
    if not WHITELIST_PATH.exists():
        logger.warning("whitelist not found: %s", WHITELIST_PATH)
        return []
    payload = json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))
    rows: list[CandidateRow] = []
    for m in payload.get("members", []):
        rows.append(
            CandidateRow(
                symbol=m["symbol"].upper(),
                source="score5_whitelist",
                category=m.get("category"),
                base_score=2.0,  # WHITELIST 보너스 가산점 기본값
                extra={"added_via": "manual_curation"},
                notes=m.get("notes"),
            )
        )
    return rows


def load_blacklist() -> set[str]:
    if not BLACKLIST_PATH.exists():
        return set()
    payload = json.loads(BLACKLIST_PATH.read_text(encoding="utf-8"))
    return {m["symbol"].upper() for m in payload.get("members", [])}


# ---------- 모멘텀 스캐너 ----------


def _liquidity_pass(bars: pd.DataFrame) -> tuple[bool, dict]:
    """가격 + 거래대금 컷. (passes, metadata) 반환."""
    if len(bars) < 30:
        return False, {"reason": "insufficient_history"}
    last30 = bars.tail(30)
    avg_dollar_vol = float((last30["close"] * last30["volume"]).mean())
    last_close = float(last30["close"].iloc[-1])
    metadata = {
        "last_close": round(last_close, 2),
        "avg_dollar_vol_30d": int(avg_dollar_vol),
    }
    if last_close < MIN_PRICE:
        return False, {**metadata, "reason": "penny"}
    if avg_dollar_vol < MIN_AVG_DOLLAR_VOL_30D:
        return False, {**metadata, "reason": "low_liquidity"}
    return True, metadata


def momentum_scan(symbols: list[str], excludes: set[str]) -> list[CandidateRow]:
    """일봉 모멘텀 스캐너 — near_52w_high + tight_base 시그널 만족 종목."""
    near = SIGNAL_REGISTRY["near_52w_high"]
    tight = SIGNAL_REGISTRY["tight_base"]

    today = date.today()
    start = (today - timedelta(days=400)).isoformat()
    end = today.isoformat()

    rows: list[CandidateRow] = []
    for symbol in symbols:
        if symbol in excludes:
            continue
        try:
            bars = get_bars(symbol, start, end, "1d")
        except Exception as exc:
            logger.debug("get_bars failed for %s: %s", symbol, exc)
            continue
        if len(bars) < near.min_bars:
            continue

        ok, meta = _liquidity_pass(bars)
        if not ok:
            continue

        try:
            near_series = near.evaluate(bars)
            tight_series = tight.evaluate(bars)
        except Exception as exc:
            logger.debug("signal eval failed for %s: %s", symbol, exc)
            continue

        last_idx = -1
        score = 0.0
        if int(near_series.iloc[last_idx]) == 1:
            score += 2.0
        if int(tight_series.iloc[last_idx]) == 1:
            score += 1.5

        if score <= 0:
            continue

        rows.append(
            CandidateRow(
                symbol=symbol,
                source="momentum_scanner",
                category="momentum",
                base_score=score,
                extra=meta,
            )
        )
    return rows


# ---------- DB upsert ----------


async def upsert_members(
    session: AsyncSession,
    rows: list[CandidateRow],
    valid_until: date,
) -> int:
    """source별로 (symbol, source) UNIQUE 기준 upsert. valid_until 항상 갱신."""
    if not rows:
        return 0
    payload = []
    now_utc = datetime.now(timezone.utc)
    for r in rows:
        payload.append(
            {
                "symbol": r.symbol,
                "source": r.source,
                "category": r.category,
                "base_score": Decimal(str(r.base_score)),
                "valid_until": valid_until,
                "added_at": now_utc,
                "last_revalidated_at": now_utc,
                "enabled": True,
                "extra": r.extra,
                "notes": r.notes,
            }
        )
    stmt = pg_insert(UniverseMember).values(payload)
    update_cols = {
        "category": stmt.excluded.category,
        "base_score": stmt.excluded.base_score,
        "valid_until": stmt.excluded.valid_until,
        "last_revalidated_at": stmt.excluded.last_revalidated_at,
        "enabled": True,
        "extra": stmt.excluded.extra,
        "notes": stmt.excluded.notes,
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_universe_symbol_source", set_=update_cols
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)


async def rolling_revalidate(session: AsyncSession) -> dict[str, int]:
    """만료된 멤버를 비활성화. 자동 demote.

    만료 후에도 momentum_scanner가 다시 추가하면 다음 upsert에서 enabled=True로 복구.
    """
    today = date.today()
    stmt = select(UniverseMember).where(
        UniverseMember.valid_until.isnot(None),
        UniverseMember.valid_until < today,
        UniverseMember.enabled == True,  # noqa: E712
    )
    result = await session.execute(stmt)
    expired = result.scalars().all()
    for m in expired:
        m.enabled = False
    await session.commit()
    return {"expired": len(expired)}


async def refresh(session: AsyncSession) -> dict:
    """월 1회 호출 — 전체 universe 재구성."""
    blacklist = load_blacklist()
    whitelist_rows = load_whitelist()

    # 모멘텀 스캐너: SP500 + whitelist union. Wikipedia 차단/네트워크 실패 시 default 50개로 폴백.
    try:
        sp500 = get_pool("sp500")
    except Exception as exc:
        logger.warning("SP500 fetch failed (%s) — falling back to 'default' 50 megacaps", exc)
        sp500 = get_pool("default")
    universe_for_scan = sorted(set(sp500) | {r.symbol for r in whitelist_rows})
    momentum_rows = momentum_scan(universe_for_scan, blacklist)

    valid_until = date.today() + timedelta(days=TTL_DAYS)
    n_white = await upsert_members(session, whitelist_rows, valid_until)
    n_momentum = await upsert_members(session, momentum_rows, valid_until)

    revalidate_result = await rolling_revalidate(session)

    summary = {
        "whitelist_added": n_white,
        "momentum_added": n_momentum,
        "expired_demoted": revalidate_result["expired"],
        "blacklist_size": len(blacklist),
        "valid_until": valid_until.isoformat(),
    }
    logger.info("Stage1 universe refresh: %s", summary)
    return summary


# ---------- CLI ----------


async def _run(args: argparse.Namespace) -> None:
    from api.db.session import async_session_factory

    async with async_session_factory() as session:
        if args.revalidate_only:
            result = await rolling_revalidate(session)
            print(json.dumps(result, indent=2))
        else:
            result = await refresh(session)
            print(json.dumps(result, indent=2))


def main() -> None:
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Stage 1 Universe (월 1회)")
    parser.add_argument("--refresh", action="store_true", default=True)
    parser.add_argument(
        "--revalidate-only",
        action="store_true",
        help="만료 멤버만 비활성화 (재스캔 없이)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
