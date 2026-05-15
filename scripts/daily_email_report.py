"""매일 아침 9 AM ET — 종합 리포트 이메일 발송.

구성:
  1) Market Regime 헤더 — SPY/QQQ/VIX + regime ON/OFF
  2) Daily Picks Top 3 + 백업 2 — Stage 2 결과 + 채택 사유 (rationale)
  3) Momentum Scanner Top 10 — 거래량+가격 모멘텀 점수 + 시그널 breakdown
  4) 직전 거래일 picks의 PnL — pivot 대비 종가 변동, target/stop 도달 여부

CLI:
  python -m scripts.daily_email_report
  python -m scripts.daily_email_report --dry-run        # 메일 미발송, HTML stdout
  python -m scripts.daily_email_report --to a@b.com     # 수신자 override
  python -m scripts.daily_email_report --date 2026-05-08

Windows Task Scheduler:
  scripts/install_daily_email_task.ps1 참조.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

import pandas as pd
import yfinance as yf
from sqlalchemy import select

from api.db import async_session_factory
from api.db.models import DailyPick, TradePlan, TradePlanOutcome
from api.routes.dashboard import build_dashboard
from notifications import EmailConfigError, send_email
from scanner.stage2_daily_picks import _serialize_picks, run_daily_picks
from scripts.scan_momentum import (
    EARNINGS_CALENDAR_PATH,
    earnings_phase,
    load_earnings_calendar,
    scan,
)

logger = logging.getLogger("daily_email_report")

INDEX_SYMBOLS = ["SPY", "QQQ", "^VIX"]


# ─────────────── 데이터 수집 ───────────────


@dataclass
class IndexQuote:
    symbol: str
    last: float | None
    pct_change: float | None  # 전일 대비 %


def fetch_index_quotes() -> list[IndexQuote]:
    """yfinance로 SPY/QQQ/VIX 최근 2거래일 종가 → 전일대비 % 계산."""
    quotes: list[IndexQuote] = []
    try:
        df = yf.download(
            INDEX_SYMBOLS,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("index quotes fetch failed: %s", exc)
        return [IndexQuote(s, None, None) for s in INDEX_SYMBOLS]

    for sym in INDEX_SYMBOLS:
        try:
            closes = df[sym]["Close"].dropna() if sym in df.columns.get_level_values(0) else df["Close"][sym].dropna()
            if len(closes) >= 2:
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                pct = (last - prev) / prev * 100 if prev else None
            elif len(closes) == 1:
                last = float(closes.iloc[-1])
                pct = None
            else:
                last, pct = None, None
        except Exception:  # noqa: BLE001
            last, pct = None, None
        quotes.append(IndexQuote(sym, last, pct))
    return quotes


async def fetch_yesterday_trade_plans(
    target_date: date,
) -> tuple[date | None, list[tuple[TradePlan, list[TradePlanOutcome]]]]:
    """target_date 직전, 사용자가 입력한 trade_plans + 그에 매핑된 outcomes."""
    from sqlalchemy.orm import selectinload

    async with async_session_factory() as session:
        prev_date_row = await session.execute(
            select(TradePlan.plan_date)
            .where(TradePlan.plan_date < target_date)
            .order_by(TradePlan.plan_date.desc())
            .limit(1)
        )
        prev_date = prev_date_row.scalar_one_or_none()
        if prev_date is None:
            return None, []
        result = await session.execute(
            select(TradePlan)
            .where(TradePlan.plan_date == prev_date)
            .options(selectinload(TradePlan.outcomes))
            .order_by(TradePlan.rank)
        )
        plans = list(result.scalars().all())
        return prev_date, [(p, list(p.outcomes)) for p in plans]


async def fetch_yesterday_picks(target_date: date) -> tuple[date | None, list[DailyPick]]:
    """target_date 직전 영업일의 DailyPick 레코드들."""
    async with async_session_factory() as session:
        # 가장 가까운 이전 pick_date
        prev_date_row = await session.execute(
            select(DailyPick.pick_date)
            .where(DailyPick.pick_date < target_date)
            .order_by(DailyPick.pick_date.desc())
            .limit(1)
        )
        prev_date = prev_date_row.scalar_one_or_none()
        if prev_date is None:
            return None, []
        result = await session.execute(
            select(DailyPick).where(DailyPick.pick_date == prev_date).order_by(DailyPick.rank)
        )
        picks = list(result.scalars().all())
        return prev_date, picks


def fetch_followup_ohlc(symbols: list[str], pick_date: date) -> dict[str, dict[str, float]]:
    """pick_date 시점에 산출된 종목들의 그 날 OHLC를 yfinance에서 가져온다.

    Returns: {symbol: {"open", "high", "low", "close"}}
    """
    if not symbols:
        return {}
    end = pick_date + timedelta(days=4)
    try:
        df = yf.download(
            symbols,
            start=pick_date.isoformat(),
            end=end.isoformat(),
            interval="1d",
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("followup OHLC fetch failed: %s", exc)
        return {}

    out: dict[str, dict[str, float]] = {}
    if len(symbols) == 1:
        sym = symbols[0]
        try:
            row = df.loc[df.index.date == pick_date].iloc[0] if not df.empty else None
        except Exception:  # noqa: BLE001
            row = None
        if row is not None:
            out[sym] = {
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            }
        return out

    # 다종목 — multi-index columns
    for sym in symbols:
        try:
            sub = df[sym] if sym in df.columns.get_level_values(0) else None
            if sub is None or sub.empty:
                continue
            mask = sub.index.date == pick_date
            if not any(mask):
                continue
            row = sub[mask].iloc[0]
            out[sym] = {
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            }
        except Exception:  # noqa: BLE001
            continue
    return out


# ─────────────── 평가 로직 ───────────────


def evaluate_pick_outcome(pick: DailyPick, ohlc: dict[str, float] | None) -> dict[str, Any]:
    """전일 pick이 target/stop을 친 흐름 평가 (이론적 — 실제 진입가정).

    pivot 대비 close % 수익률, 1R/2R/stop 도달 여부 표시.
    """
    pivot = float(pick.pivot_price)
    stop = float(pick.stop_price)
    t1 = float(pick.target_1r)
    t2 = float(pick.target_2r)
    risk = pivot - stop if pivot > stop else None

    if ohlc is None:
        return {
            "pivot": pivot,
            "stop": stop,
            "t1r": t1,
            "t2r": t2,
            "close": None,
            "high": None,
            "low": None,
            "ret_pct": None,
            "r_multiple": None,
            "outcome": "데이터 없음",
        }

    high, low, close = ohlc["high"], ohlc["low"], ohlc["close"]
    hit_stop = low <= stop
    hit_t1 = high >= t1
    hit_t2 = high >= t2

    if hit_t2:
        outcome = "🟢 +2R 도달"
    elif hit_t1:
        outcome = "🟢 +1R 도달"
    elif hit_stop:
        outcome = "🔴 손절"
    else:
        outcome = "⚪ 미도달"

    ret_pct = (close - pivot) / pivot * 100 if pivot else None
    r_mult = (close - pivot) / risk if risk and risk > 0 else None

    return {
        "pivot": pivot,
        "stop": stop,
        "t1r": t1,
        "t2r": t2,
        "close": close,
        "high": high,
        "low": low,
        "ret_pct": ret_pct,
        "r_multiple": r_mult,
        "outcome": outcome,
    }


# ─────────────── HTML 빌더 ───────────────


CSS = """
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; color: #1f2937; max-width: 900px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 8px; }
  h2 { font-size: 17px; margin: 24px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #e5e7eb; }
  .meta { color: #6b7280; font-size: 12px; margin-bottom: 16px; }
  .regime { padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
  .regime.on { background: #ecfdf5; border: 1px solid #10b981; }
  .regime.off { background: #fef2f2; border: 1px solid #ef4444; }
  .regime.unknown { background: #f3f4f6; border: 1px solid #9ca3af; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { padding: 6px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; }
  th { background: #f9fafb; font-weight: 600; color: #374151; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.sym { font-weight: 600; }
  .pos { color: #059669; }
  .neg { color: #dc2626; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .badge.day { background: #dbeafe; color: #1e40af; }
  .badge.swing { background: #fef3c7; color: #92400e; }
  .badge.backup { background: #f3f4f6; color: #4b5563; }
  .badge.tier-s { background: #ecfdf5; color: #047857; }
  .badge.tier-a { background: #eff6ff; color: #1d4ed8; }
  .badge.tier-b { background: #fefce8; color: #92400e; }
  .badge.tier-c { background: #f3f4f6; color: #4b5563; }
  .rationale { color: #4b5563; font-size: 12px; }
  .small { font-size: 11px; color: #6b7280; }
  .empty { color: #9ca3af; font-style: italic; padding: 12px 0; }
  h3.tier { font-size: 14px; margin: 16px 0 6px; color: #111827; }
  .tier-summary { color: #6b7280; font-size: 12px; margin: 4px 0 12px; }
</style>
"""


def _fmt_money(v: float | None, prec: int = 2) -> str:
    return f"${v:,.{prec}f}" if v is not None else "—"


def _fmt_pct(v: float | None, prec: int = 2, signed: bool = True) -> str:
    if v is None:
        return "—"
    sign = "+" if signed and v > 0 else ""
    return f"{sign}{v:.{prec}f}%"


def _pct_class(v: float | None) -> str:
    if v is None:
        return ""
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def render_regime_block(regime_on: bool | None, quotes: list[IndexQuote]) -> str:
    if regime_on is True:
        cls, label = "on", "✅ ON — Long 진입 우호"
    elif regime_on is False:
        cls, label = "off", "🛑 OFF — Long 진입 비추천"
    else:
        cls, label = "unknown", "❓ 판정 불가"

    quote_strs = []
    for q in quotes:
        last_str = _fmt_money(q.last)
        pct_str = _fmt_pct(q.pct_change)
        cls_q = _pct_class(q.pct_change)
        quote_strs.append(
            f"<b>{escape(q.symbol)}</b> {last_str} "
            f"<span class='{cls_q}'>({pct_str})</span>"
        )

    return f"""
    <div class="regime {cls}">
      <b>Market Regime:</b> {label}<br/>
      <span class="small">{' &nbsp;|&nbsp; '.join(quote_strs)}</span>
    </div>
    """


def _format_rationale(rationale: Any) -> str:
    """rationale dict → 사람이 읽기 좋은 짧은 칩 문자열.

    dict인 경우 의미 있는 핵심 시그널만 추려서 ' · ' 구분으로 노출.
    """
    if rationale is None or rationale == "":
        return ""
    if isinstance(rationale, str):
        return rationale
    if isinstance(rationale, list):
        return " · ".join(str(x) for x in rationale)
    if not isinstance(rationale, dict):
        return str(rationale)

    chips: list[str] = []
    # 통과/구분 (bool)
    if rationale.get("is_whitelist"):
        chips.append("✓ Whitelist")
    if rationale.get("stage2_pass"):
        chips.append("✓ Stage2")
    if rationale.get("breakout_20d"):
        chips.append("✓ 20d Breakout")
    if rationale.get("near_52w_high"):
        chips.append("✓ 52w High")
    if rationale.get("tight_flag"):
        chips.append("✓ Tight Flag")
    if rationale.get("compression"):
        chips.append("✓ Compression")
    if rationale.get("sector_aligned"):
        chips.append("✓ Sector aligned")

    # 숫자 지표
    rs = rationale.get("rs_percentile")
    if rs is not None:
        chips.append(f"RS {rs:.0f}/100")
    rsi = rationale.get("rsi_14")
    if rsi is not None:
        chips.append(f"RSI {rsi:.0f}")
    gap = rationale.get("gap_pct")
    if gap is not None:
        chips.append(f"Gap {gap:+.1f}%")
    vol_ratio = rationale.get("volume_vs_avg")
    if vol_ratio is not None:
        chips.append(f"Vol {vol_ratio:.2f}x")
    regime_score = rationale.get("regime_score")
    if regime_score is not None:
        chips.append(f"Regime {regime_score:.0f}")

    # catalyst kind
    ck = rationale.get("catalyst_kind")
    if ck and ck != "none":
        chips.append(f"Catalyst: {ck}")

    return " · ".join(chips) if chips else "(no signals)"


def render_picks_table(picks_payload: list[dict[str, Any]]) -> str:
    if not picks_payload:
        return "<p class='empty'>오늘 hard gate 통과한 종목이 없습니다.</p>"

    rows = []
    for p in picks_payload:
        is_backup = p.get("is_backup", False)
        rank_label = f"백업 {p['rank']-3}" if is_backup else f"#{p['rank']}"
        tag = p.get("strategy_tag", "day")
        sector = p.get("sector") or "—"
        rationale = _format_rationale(p.get("rationale"))
        catalyst = p.get("catalyst") or "—"

        rows.append(f"""
        <tr>
          <td>{escape(rank_label)}</td>
          <td class="sym">{escape(p['symbol'])}
            <span class="badge {escape(tag)}">{escape(tag)}</span>
            {"<span class='badge backup'>backup</span>" if is_backup else ""}
          </td>
          <td class="num">{p['score']:.1f}</td>
          <td class="num">{_fmt_money(p['pivot'])}</td>
          <td class="num">{_fmt_money(p['stop'])}</td>
          <td class="num">{_fmt_money(p['target_1r'])}</td>
          <td class="num">{_fmt_money(p['target_2r'])}</td>
          <td class="num">{p.get('rvol', 0):.2f}x</td>
          <td>{escape(sector)}</td>
          <td class="rationale">{escape(str(catalyst))}<br/><span class="small">{escape(str(rationale))}</span></td>
        </tr>""")

    return f"""
    <table>
      <thead>
        <tr>
          <th>순위</th><th>종목</th><th>스코어</th>
          <th>Pivot</th><th>Stop</th><th>+1R</th><th>+2R</th>
          <th>RVOL</th><th>섹터</th><th>Catalyst & 사유</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def render_integrated_table(picks: list[Any]) -> str:
    """통합 시스템 v4 (v3 priority + PEAD bonus + golden setup amplifier) 오늘 picks 렌더."""
    if not picks:
        return "<p class='empty'>통합 시스템 picks 없음 (regime 차단 또는 quality fail).</p>"

    rows = []
    for p in picks:
        m = p.score_meta or {}
        sector = p.sector or "—"

        # 핵심 점수
        tier = m.get("tier", "?")
        tier_label = "Tier 1" if tier == 1 else "Tier 2" if tier == 2 else "—"
        scanner_score = m.get("scanner_score", 0) or 0
        v3_score = m.get("v3_score", 0) or 0

        # 셋업
        is_compression = m.get("compression", False)
        is_expansion = m.get("expansion", False)
        is_golden = m.get("golden_setup", False)
        ce_parts = []
        if is_golden:
            ce_parts.append("🌟 Golden")
        elif is_compression:
            ce_parts.append("압축")
        elif is_expansion:
            ce_parts.append("폭발")
        ce_label = " ".join(ce_parts) or "—"

        # RSI
        rsi_grade = m.get("rsi_grade", "—")
        rsi_value = m.get("rsi_value", 0) or 0

        # 시그널 칩 (보너스 종류 표시)
        chips = []
        if m.get("v3_passed"):
            chips.append("V3✓")
        if m.get("stage2_pass"):
            chips.append("Stage2")
        if (m.get("pead_bonus") or 0) > 0:
            chips.append("📈 PEAD")
        if (m.get("confluence_bonus") or 0) > 0:
            chips.append("🔗 Confluence")
        if (m.get("sector_bonus") or 0) > 0:
            chips.append("섹터+10")
        signals = " · ".join(chips) or "—"

        rows.append(f"""
        <tr>
          <td>#{p.rank}<br/><span class="small">{escape(tier_label)}</span></td>
          <td class="sym">{escape(p.symbol)}</td>
          <td class="num"><b>{p.score:.1f}</b></td>
          <td class="num">{float(scanner_score):.1f}/5</td>
          <td class="num">{float(v3_score):.1f}/100</td>
          <td>{escape(ce_label)}</td>
          <td class="num">{float(rsi_value):.1f}<br/><span class="small">({escape(rsi_grade)})</span></td>
          <td>{escape(sector)}</td>
          <td class="rationale">{escape(signals)}</td>
        </tr>""")

    return f"""
    <table>
      <thead>
        <tr>
          <th>순위</th><th>종목</th><th>Composite</th>
          <th>Scanner</th><th>V3</th>
          <th>셋업</th><th>RSI(14)</th>
          <th>섹터</th><th>보너스/시그널</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <p class="small">
      <b>Composite</b> = v3 quadratic + Compression + Open Location + RSI + Stage2 보너스 (8) + 섹터 (10)
      + Confluence (15, scanner≥4 동조) + PEAD (12, 실적 직후) + Golden Setup (10, 압축+폭발 동시).
      <b>Tier 1</b>: v3 priority. <b>Tier 2</b>: scanner-only with 2+ quality signals (엄격).
    </p>
    """


def render_momentum_table(
    ranked: list[dict[str, Any]],
    top: int,
    earnings_data: dict[str, dict] | None,
) -> str:
    rows_data = ranked[:top]
    if not rows_data:
        return "<p class='empty'>모멘텀 후보가 없습니다.</p>"

    rows = []
    for i, r in enumerate(rows_data, 1):
        s = r["signals"]
        # 발화한 시그널만 칩으로 표시
        active = [name.replace("_", " ") for name, v in s.items() if v > 0]
        chips = " · ".join(escape(a) for a in active) or "—"

        ear_phase = r.get("earnings_phase", "clean")
        ear_emoji = {"pre": "🚨", "post": "📈", "clean": ""}.get(ear_phase, "")
        ear_label = f"{ear_emoji} {ear_phase}" if ear_emoji else "clean"

        rows.append(f"""
        <tr>
          <td>{i}</td>
          <td class="sym">{escape(r['symbol'])}</td>
          <td class="num">{_fmt_money(r['close'])}</td>
          <td class="num">{r.get('vol_vs_20d_avg') or 0:.2f}x</td>
          <td class="num"><b>{r['total_score']}</b></td>
          <td class="num">{r['volume_score']}/{r['momentum_score']}</td>
          <td>{chips}</td>
          <td class="small">{escape(ear_label)}</td>
        </tr>""")

    note = ""
    if earnings_data is None:
        note = "<p class='small'>※ earnings calendar 미발견 — pre/post 분류 비활성.</p>"

    return f"""
    <table>
      <thead>
        <tr>
          <th>#</th><th>종목</th><th>종가</th><th>vol/20d</th>
          <th>총점</th><th>vol/mom</th><th>발화 시그널</th><th>실적</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    {note}
    """


def render_pnl_table(
    prev_date: date | None,
    picks: list[DailyPick],
    ohlc_map: dict[str, dict[str, float]],
) -> str:
    if prev_date is None or not picks:
        return "<p class='empty'>직전 거래일 picks 기록이 없습니다.</p>"

    rows = []
    for pk in picks:
        ohlc = ohlc_map.get(pk.symbol)
        ev = evaluate_pick_outcome(pk, ohlc)
        ret_cls = _pct_class(ev["ret_pct"])
        r_str = f"{ev['r_multiple']:+.2f}R" if ev["r_multiple"] is not None else "—"
        rows.append(f"""
        <tr>
          <td>#{pk.rank}{' (backup)' if pk.is_backup else ''}</td>
          <td class="sym">{escape(pk.symbol)}</td>
          <td class="num">{_fmt_money(ev['pivot'])}</td>
          <td class="num">{_fmt_money(ev['stop'])}</td>
          <td class="num">{_fmt_money(ev['close'])}</td>
          <td class="num {ret_cls}">{_fmt_pct(ev['ret_pct'])}</td>
          <td class="num {ret_cls}">{escape(r_str)}</td>
          <td>{ev['outcome']}</td>
        </tr>""")

    return f"""
    <p class="small">기준일: <b>{prev_date.isoformat()}</b> — pivot 대비 당일 종가 기준 (이론값, 실제 체결 미반영).</p>
    <table>
      <thead>
        <tr>
          <th>순위</th><th>종목</th><th>Pivot</th><th>Stop</th>
          <th>당일 종가</th><th>수익률</th><th>R-multiple</th><th>결과</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def render_trade_plan_pnl(
    prev_date: date | None,
    plans_with_outcomes: list[tuple[TradePlan, list[TradePlanOutcome]]],
) -> str:
    """어제 사용자가 입력한 매매 Plan + 1d 실현 수익."""
    if prev_date is None or not plans_with_outcomes:
        return (
            "<p class='empty'>"
            "어제 입력한 매매 Plan이 없습니다. 추천을 받으려면 "
            "<b>오늘의 매매 Plan</b> 페이지에서 종목별 금액을 입력하세요."
            "</p>"
        )

    rows = []
    total_amount = 0.0
    total_pnl = 0.0
    n_with_outcome = 0
    for pl, outcomes in plans_with_outcomes:
        amount = float(pl.amount_usd)
        shares = pl.shares or 0
        entry = float(pl.entry_price)
        stop = float(pl.stop_price)
        risk = float(pl.risk_usd or 0)
        sector = pl.sector or "—"
        total_amount += amount

        # 1d outcome 우선
        oc_1d = next((o for o in outcomes if o.horizon_days == 1), None)
        if oc_1d is not None:
            pnl = float(oc_1d.realized_pnl_usd or 0)
            ret_pct = float(oc_1d.pct_return) * 100
            alpha_pct = float(oc_1d.alpha) * 100
            exit_str = _fmt_money(float(oc_1d.exit_price))
            n_with_outcome += 1
            total_pnl += pnl
            badges = []
            if oc_1d.hit_target_1r and oc_1d.hit_target_2r:
                badges.append("<span class='badge tier-s'>🎯🎯 1차+2차 도달 (+1.5R)</span>")
            elif oc_1d.hit_target_1r:
                if oc_1d.hit_stop:
                    badges.append("<span class='badge tier-b'>🎯 1차 청산 + 잔여 손절 (~0R)</span>")
                else:
                    badges.append("<span class='badge tier-s'>🎯 1차 청산 (+1R, 잔여 보유)</span>")
            if oc_1d.hit_stop and not oc_1d.hit_target_1r:
                badges.append("<span class='badge tier-c'>🔴 전량 손절 (-1R)</span>")
            # 부분 청산 PnL 표시 (있으면)
            partial_pnl = float(oc_1d.partial_realized_pnl_usd or 0)
            if partial_pnl != 0 and abs(partial_pnl - pnl) > 0.01:
                badges.append(f"<span class='small'>부분청산 PnL: {'+' if partial_pnl > 0 else ''}{_fmt_money(partial_pnl)}</span>")
            outcome_html = f"""
              <td class="num">{exit_str}</td>
              <td class="num {_pct_class(ret_pct)}">{_fmt_pct(ret_pct)}</td>
              <td class="num {_pct_class(alpha_pct)}">{_fmt_pct(alpha_pct)}</td>
              <td class="num {_pct_class(pnl)}">{'+' if pnl > 0 else ''}{_fmt_money(pnl)}</td>
              <td>{' '.join(badges) or '—'}</td>"""
        else:
            outcome_html = (
                "<td class='num'>—</td><td class='num'>—</td>"
                "<td class='num'>—</td><td class='num'>—</td>"
                "<td><span class='small'>대기 (16:35 backfill 후)</span></td>"
            )

        rows.append(f"""
        <tr>
          <td>#{pl.rank}</td>
          <td class="sym">{escape(pl.symbol)}<br/><span class="small">{escape(sector)}</span></td>
          <td class="num">{_fmt_money(amount)}</td>
          <td class="num">{shares}주</td>
          <td class="num">{_fmt_money(entry)}</td>
          <td class="num neg">{_fmt_money(stop)}<br/><span class="small">위험 ${risk:,.2f}</span></td>
          {outcome_html}
        </tr>""")

    summary_pnl = (
        f"<b class='{_pct_class(total_pnl)}'>"
        f"{'+' if total_pnl > 0 else ''}{_fmt_money(total_pnl)}</b>"
        if n_with_outcome > 0
        else "<span class='small'>1d outcome 대기 중</span>"
    )

    return f"""
    <p class="small">
      기준일 <b>{prev_date.isoformat()}</b> · 입력 plan {len(plans_with_outcomes)}개 ·
      총 입력 {_fmt_money(total_amount)} · 1d 실손익 {summary_pnl}
    </p>
    <table>
      <thead>
        <tr>
          <th>순위</th><th>종목/섹터</th>
          <th>입력 $</th><th>주식수</th>
          <th>진입</th><th>손절(위험)</th>
          <th>1d 종가</th><th>1d 수익률</th>
          <th>SPY 알파</th><th>실손익</th>
          <th>도달</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <p class="small">
      ※ 1d 수익률은 시가→종가 (yfinance 일봉) 기준. 실제 체결가는 사용자 Webull 매매 결과로 확인 필요.
      5d/10d outcome은 backfill cron이 영업일 경과 후 자동 채움.
    </p>
    """


_TIER_META: dict[str, dict[str, str]] = {
    "S": {"emoji": "🥇", "label": "Tier S", "sub": "최고 신뢰 — 시그널 + 백테스트 + 진입 안전"},
    "A": {"emoji": "🥈", "label": "Tier A", "sub": "강한 후보 — 시그널 또는 PEAD 알파"},
    "B": {"emoji": "🥉", "label": "Tier B", "sub": "보조 후보 — WHITELIST 검증 + 안전 진입"},
    "C": {"emoji": "👀", "label": "Tier C", "sub": "관찰 — 시그널 약하거나 검증 부족"},
}

_PHASE_LABEL: dict[str, str] = {
    "pre": "🚨 실적 임박",
    "post": "📈 실적 직후",
    "clean": "",
}


def _top_reasons(reasons: list[Any], k: int = 2) -> str:
    """Reason 객체 리스트(긍정 우선)에서 상위 k개를 칩 문자열로."""
    if not reasons:
        return "—"
    pos = [r for r in reasons if getattr(r, "polarity", "") == "positive"]
    neg = [r for r in reasons if getattr(r, "polarity", "") == "negative"]
    picked = (pos + neg)[:k]
    chips: list[str] = []
    for r in picked:
        polarity = getattr(r, "polarity", "neutral")
        prefix = "▲" if polarity == "positive" else ("▼" if polarity == "negative" else "•")
        chips.append(f"{prefix} {escape(str(r.label))}")
    return " · ".join(chips) if chips else "—"


def _render_tier_rows(candidates: list[Any]) -> str:
    rows = []
    for c in candidates:
        lvl = c.levels
        phase = _PHASE_LABEL.get(c.earnings_phase, "")
        sector = c.sector or "—"
        rvol = c.vol_vs_20d_avg or 0.0
        if lvl is None:
            entry_str = stop_str = t1_str = t2_str = qty_str = "—"
            risk_str = ""
        else:
            entry_str = _fmt_money(lvl.entry)
            stop_str = _fmt_money(lvl.stop)
            t1_str = _fmt_money(lvl.target_1r)
            t2_str = _fmt_money(lvl.target_2r)
            qty_str = f"{lvl.qty}주" if lvl.qty else "—"
            risk_str = f"<span class='small'>위험 {lvl.risk_pct:.2f}%</span>"

        hist = c.historical or {}
        hit = hist.get("hit_rate")
        n = hist.get("n")
        hist_str = (
            f"{int(hit * 100)}%/{n}회" if hit is not None and n else "—"
        )

        reasons_str = _top_reasons(c.reasons, k=2)
        phase_html = (
            f"<br/><span class='small'>{escape(phase)}</span>" if phase else ""
        )

        rows.append(f"""
        <tr>
          <td>#{c.rank}</td>
          <td class="sym">{escape(c.symbol)}<br/><span class="small">{escape(sector)}</span></td>
          <td class="num"><b>{c.total_score}</b></td>
          <td class="num">{entry_str}</td>
          <td class="num neg">{stop_str}</td>
          <td class="num pos">{t1_str}</td>
          <td class="num pos">{t2_str}</td>
          <td class="num">{qty_str}<br/>{risk_str}</td>
          <td class="num">{rvol:.2f}x</td>
          <td>{hist_str}</td>
          <td class="rationale">{reasons_str}{phase_html}</td>
        </tr>""")
    return "".join(rows)


def render_dashboard_block(dashboard: Any) -> str:
    """통합 대시보드 응답 → Tier별 카드 테이블."""
    if dashboard is None:
        return "<p class='empty'>대시보드 데이터를 가져오지 못했습니다.</p>"

    cfg = dashboard.config
    summary = (
        f"기준일 <b>{escape(dashboard.as_of)}</b> · "
        f"후보 {dashboard.n_candidates}개 "
        f"(🥇{dashboard.n_tier_s} 🥈{dashboard.n_tier_a} 🥉{dashboard.n_tier_b} 👀{dashboard.n_tier_c}) · "
        f"점수≥{cfg['score_min']} · ATR×{cfg['atr_mult']} · "
        f"자본 ${cfg['equity']:,.0f} / 위험 {cfg['risk_per_trade']*100:.2f}%"
    )

    blocks = [f"<p class='small'>{summary}</p>"]

    has_any = False
    for tier_key in ("S", "A", "B"):
        cands = dashboard.tiers.get(tier_key, [])
        meta = _TIER_META[tier_key]
        if not cands:
            blocks.append(
                f"<h3 class='tier'>{meta['emoji']} {meta['label']} "
                f"<span class='small'>(0)</span></h3>"
                f"<p class='empty'>해당 등급 후보 없음.</p>"
            )
            continue

        has_any = True
        blocks.append(
            f"<h3 class='tier'>{meta['emoji']} {meta['label']} "
            f"<span class='small'>({len(cands)})</span></h3>"
            f"<p class='tier-summary'>{escape(meta['sub'])}</p>"
            f"""<table>
              <thead>
                <tr>
                  <th>#</th><th>종목/섹터</th><th>점수</th>
                  <th>진입</th><th>손절</th><th>+1R</th><th>+2R</th>
                  <th>주식수</th><th>RVOL</th><th>적중/표본</th><th>핵심 사유</th>
                </tr>
              </thead>
              <tbody>{_render_tier_rows(cands)}</tbody>
            </table>"""
        )

    n_c = len(dashboard.tiers.get("C", []))
    if n_c:
        blocks.append(
            f"<p class='small'>👀 Tier C 관찰 후보 <b>{n_c}개</b> — 본 메일에서 표 생략 (대시보드 화면에서 확인).</p>"
        )

    if not has_any and n_c == 0:
        blocks.append("<p class='empty'>조건에 맞는 후보가 없습니다.</p>")

    return "\n".join(blocks)


def render_html(
    target_date: date,
    regime_on: bool | None,
    quotes: list[IndexQuote],
    picks_payload: list[dict[str, Any]],
    momentum_top10: list[dict[str, Any]],
    earnings_data: dict[str, dict] | None,
    prev_date: date | None,
    prev_picks: list[DailyPick],
    prev_ohlc: dict[str, dict[str, float]],
    dashboard: Any | None = None,
    integrated_picks: list[Any] | None = None,
    plan_prev_date: date | None = None,
    plan_with_outcomes: list[tuple[TradePlan, list[TradePlanOutcome]]] | None = None,
) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">{CSS}</head><body>
      <h1>📊 Daily Stock Report — {target_date.isoformat()}</h1>
      <div class="meta">
        Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (local) ·
        scan_momentum + stage2_daily_picks + integrated
      </div>

      {render_regime_block(regime_on, quotes)}

      <h2>🎯 통합 대시보드 — Tier 분류</h2>
      {render_dashboard_block(dashboard)}

      <h2>🌟 통합 시스템 (Integrated) — Top 5</h2>
      <p class="small">scan_momentum 검증 알파(OOS Sharpe 3.01) + v3 quality layer (Compression/Open Location/RSI 구조) + 섹터 집중 보너스</p>
      {render_integrated_table(integrated_picks or [])}

      <h2>🎯 v3 Daily Picks (Top 3 + 백업 2)</h2>
      {render_picks_table(picks_payload)}

      <h2>🔍 Momentum Scanner — Top 10</h2>
      {render_momentum_table(momentum_top10, 10, earnings_data)}

      <h2>💰 어제 입력한 매매 Plan 결과</h2>
      {render_trade_plan_pnl(plan_prev_date, plan_with_outcomes or [])}

      <h2>📈 직전 거래일 Picks 결과 (이론값)</h2>
      {render_pnl_table(prev_date, prev_picks, prev_ohlc)}

      <p class="small" style="margin-top:32px">
        ※ 본 리포트는 자동 생성된 참고 자료이며 매매 권유가 아닙니다.
        모든 진입은 본인 책임. — generated by scripts/daily_email_report.py
      </p>
    </body></html>"""


# ─────────────── 오케스트레이션 ───────────────


async def build_report(target_date: date, equity: float) -> str:
    """모든 데이터 수집 → HTML 빌드."""
    # 1) Index quotes (sync, blocking — 짧음)
    quotes = await asyncio.to_thread(fetch_index_quotes)

    # 2) Momentum scan
    logger.info("scan_momentum 실행 중...")
    ranked, scan_meta = await scan(target_date)
    regime_on = scan_meta.get("regime_on")
    earnings_data = load_earnings_calendar(EARNINGS_CALENDAR_PATH)
    if earnings_data is not None:
        for r in ranked:
            r["earnings_phase"] = earnings_phase(
                r["symbol"], date.fromisoformat(r["as_of"]), earnings_data
            )

    momentum_top10 = ranked[:10]

    # 3) Daily picks
    logger.info("stage2_daily_picks 실행 중...")
    async with async_session_factory() as session:
        picks = await run_daily_picks(session, target_date, account_equity=equity)
    picks_payload = _serialize_picks(picks)

    # 4) 직전 거래일 PnL
    logger.info("직전 거래일 PnL 조회 중...")
    prev_date, prev_picks = await fetch_yesterday_picks(target_date)
    prev_ohlc: dict[str, dict[str, float]] = {}
    if prev_date is not None and prev_picks:
        symbols = sorted({p.symbol for p in prev_picks})
        prev_ohlc = await asyncio.to_thread(fetch_followup_ohlc, symbols, prev_date)

    # 5) 통합 대시보드 (Tier 분류 + 운용 정보)
    logger.info("통합 대시보드 빌드 중...")
    dashboard = None
    try:
        dashboard = await build_dashboard(
            target_date=target_date,
            equity=equity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("대시보드 빌드 실패(무시): %s", exc)

    # 5b) 어제 사용자가 입력한 매매 plan + 1d outcome
    logger.info("어제 매매 plan 조회 중...")
    plan_prev_date, plan_with_outcomes = await fetch_yesterday_trade_plans(target_date)

    # 6) 통합 시스템 picks (scanner + v3 quality layer)
    logger.info("통합 시스템 picks 산출 중...")
    integrated_picks: list[Any] = []
    try:
        from scanner.comparison.adapters import fetch_integrated_picks

        async with async_session_factory() as session:
            integrated_picks = await fetch_integrated_picks(session, target_date, top=5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("통합 picks 산출 실패(무시): %s", exc)

    return render_html(
        target_date=target_date,
        regime_on=regime_on,
        quotes=quotes,
        picks_payload=picks_payload,
        momentum_top10=momentum_top10,
        earnings_data=earnings_data,
        prev_date=prev_date,
        prev_picks=prev_picks,
        prev_ohlc=prev_ohlc,
        dashboard=dashboard,
        integrated_picks=integrated_picks,
        plan_prev_date=plan_prev_date,
        plan_with_outcomes=plan_with_outcomes,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily stock email report (regime + scanner + picks + 어제 PnL)")
    p.add_argument("--date", help="기준 날짜 YYYY-MM-DD (default: today)")
    p.add_argument("--equity", type=float, default=25_000.0, help="position sizing용 계좌 자본 (default 25000)")
    p.add_argument("--to", help="수신자 override (콤마 구분). 미지정 시 .env EMAIL_TO 또는 GMAIL_USER")
    p.add_argument("--dry-run", action="store_true", help="이메일 발송하지 않고 HTML을 stdout으로 출력")
    p.add_argument("--save-html", help="HTML을 파일로도 저장 (디버깅용)")
    return p.parse_args()


async def amain() -> int:
    args = parse_args()
    target = date.fromisoformat(args.date) if args.date else date.today()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        html = await build_report(target, args.equity)
    except Exception as exc:  # noqa: BLE001
        logger.error("리포트 빌드 실패: %s", exc)
        traceback.print_exc()
        # 실패 시에도 알림 발송 (dry-run 아닌 경우)
        if not args.dry_run:
            try:
                fail_html = (
                    f"<h2>⚠️ Daily report 생성 실패 — {target.isoformat()}</h2>"
                    f"<pre>{escape(traceback.format_exc())}</pre>"
                )
                send_email(
                    subject=f"[Stock] ❌ Daily report 실패 — {target.isoformat()}",
                    html_body=fail_html,
                    to=args.to,
                )
            except Exception as send_exc:  # noqa: BLE001
                logger.error("실패 알림 발송도 실패: %s", send_exc)
        return 1

    if args.save_html:
        Path(args.save_html).write_text(html, encoding="utf-8")
        logger.info("HTML saved → %s", args.save_html)

    if args.dry_run:
        sys.stdout.write(html)
        return 0

    subject = f"[Stock] Daily Report — {target.isoformat()}"
    try:
        send_email(subject=subject, html_body=html, to=args.to)
    except EmailConfigError as exc:
        logger.error("이메일 설정 오류: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.error("이메일 발송 실패: %s", exc)
        return 3

    logger.info("이메일 발송 완료 → %s", args.to or "(env)")
    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
