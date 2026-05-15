"""Phase 5: Intraday Confirm — 09:45 ET 호출.

오늘 watchlist (TradePlan.confirm_status='watchlist') 종목에 대해:
  1. 09:30~09:44 ET 1m bars로 ORB+VWAP+intraday RVOL 평가
  2. 4-pass 통과: ORB high 돌파 + VWAP 위 + RVOL ≥ 1.5x + range ≥ 0.5%
  3. ORB 기반 entry/stop/target 재계산
  4. Position size 재계산 (equity × INTRADAY_RISK_PCT / R × regime_mult × gap_penalty)
  5. confirm_status 갱신 ('passed' / 'failed')
  6. Top 3 'passed' → bracket order 발송 ('sent')

CLI:
    python -m scripts.intraday_confirm
    python -m scripts.intraday_confirm --date 2026-05-10
    python -m scripts.intraday_confirm --dry-run

안전장치 (run_trade와 동일):
  - AUTO_TRADE_ENABLED=false → dry-run
  - regime defensive → 차단
  - account.trading_blocked → 차단
  - position cap 5종목
  - daily loss limit -3% / -5%
  - 동일 symbol 보유/pending skip
  - sector cap 2종목
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "intraday_confirm"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"{date.today().isoformat()}.log", encoding="utf-8"
        ),
    ],
)

logger = logging.getLogger("intraday_confirm")


INTRADAY_RISK_PCT = float(os.environ.get("INTRADAY_RISK_PCT", "0.003"))   # 0.3% per trade
ENTRY_OFFSET = float(os.environ.get("INTRADAY_ENTRY_OFFSET", "0.05"))     # $0.05 above OR high
INTRADAY_PICK_CAP = int(os.environ.get("INTRADAY_PICK_CAP", "3"))         # top 3 단타


def _gap_penalty(gap_pct: float) -> float:
    """프리마켓 갭에 따른 sizing penalty."""
    if gap_pct is None:
        return 1.0
    if gap_pct > 10.0:
        return 0.0
    if gap_pct > 5.0:
        return 0.7
    return 1.0


async def _fetch_intraday_bars(symbol: str):
    """yfinance 1m bars — 오늘 + 직전 5~7거래일.

    yfinance는 1m bars를 최대 7일 제공.
    Staleness 가드: 마지막 bar가 YFINANCE_STALENESS_MAX_MIN(기본 30분) 초과 시 (None, None) 반환.
    confirm phase는 09:45 ET 호출이라 정상이면 last bar가 9:44 → age 1분.
    """
    from datetime import timezone as _tz

    import pandas as pd
    import yfinance as yf

    try:
        df = yf.download(
            symbol, period="7d", interval="1m", progress=False, auto_adjust=False
        )
        if df is None or df.empty:
            return None, None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
    except Exception as exc:
        logger.warning("yfinance 1m fetch failed for %s: %s", symbol, exc)
        return None, None

    # today / historical 분리
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    # Staleness 가드 — last bar가 N분 이상 오래됐으면 stale로 취급.
    last_bar_ts = df.index.max()
    age_min = (datetime.now(_tz.utc) - last_bar_ts.to_pydatetime()).total_seconds() / 60
    staleness_max_min = float(os.environ.get("YFINANCE_STALENESS_MAX_MIN", "30"))
    if age_min > staleness_max_min:
        logger.warning(
            "[stale] %s last bar %s is %.0fmin old (> %.0fmin) — skip",
            symbol, last_bar_ts.isoformat(), age_min, staleness_max_min,
        )
        return None, None

    et = df.set_index(df.index.tz_convert("America/New_York"))
    today_date = date.today()
    today_mask = et.index.date == today_date
    today_bars = et[today_mask]
    hist_bars = et[~today_mask]
    return today_bars, hist_bars


async def run_confirm(target: date, *, dry_run: bool = False) -> dict:
    """Phase 5 entrypoint. watchlist → ORB 평가 → 통과한 top N bracket order 발송."""
    from sqlalchemy import select

    from api.db.models import TradePlan
    from api.db.session import async_session_factory
    from broker_adapter import get_adapter
    from broker_adapter.alpaca_adapter import _penny
    from broker_adapter.base import BracketOrderRequest, qty_split_50_50
    from scanner.regime import evaluate_regime
    from signals.opening_range import compute_entry_stop_target, evaluate_orb
    from scripts.daily_pipeline import _check_daily_loss_limit

    auto_trade_enabled = (
        os.environ.get("AUTO_TRADE_ENABLED", "false").lower() == "true"
    ) and not dry_run

    out: dict = {
        "date": target.isoformat(),
        "auto_trade_enabled": auto_trade_enabled,
        "dry_run": dry_run,
        "evaluations": [],
        "orders": [],
        "skipped": [],
    }

    # 사용자 정책 (2026-05-13): "plan에 없는 것은 주문하지 말아"
    # → orb_auto 자동 발송은 default OFF. /trading에 직접 입력한 user_fixed plan만 09:30에 발송.
    # 다시 켜려면 .env에 AUTO_CONFIRM_DISPATCH=true 추가.
    auto_confirm_dispatch = (
        os.environ.get("AUTO_CONFIRM_DISPATCH", "false").strip().lower() == "true"
    )
    if not auto_confirm_dispatch:
        out["status"] = "disabled"
        out["reason"] = (
            "AUTO_CONFIRM_DISPATCH=false — orb_auto 자동 발송 비활성화. "
            "/trading에서 직접 입력(user_fixed)한 plan만 09:30 cron에서 발송됨."
        )
        logger.info("[confirm] SKIPPED — AUTO_CONFIRM_DISPATCH=false (사용자 정책)")
        return out

    # 1) watchlist 로드 — dispatch_mode='orb_auto' 만 (user_fixed는 09:30 run_trade가 처리)
    async with async_session_factory() as session:
        stmt = (
            select(TradePlan)
            .where(TradePlan.plan_date == target)
            .where(TradePlan.confirm_status == "watchlist")
            .where(TradePlan.dispatch_mode == "orb_auto")
            .order_by(TradePlan.rank)
        )
        plans = list((await session.execute(stmt)).scalars().all())

    out["watchlist_count"] = len(plans)
    if not plans:
        out["status"] = "no_watchlist"
        logger.info("[confirm] no watchlist plans for %s", target)
        return out

    # 2) Regime gate
    regime = evaluate_regime(target)
    out["regime_score"] = regime.score
    out["regime_mode"] = regime.mode
    if regime.long_blocked():
        out["status"] = "blocked"
        out["reason"] = f"regime defensive (score={regime.score:.0f}/15)"
        logger.warning("[confirm] BLOCKED: %s", out["reason"])
        # watchlist 상태로 남겨두지 않고 'failed' 처리 (사용자 혼동 방지)
        async with async_session_factory() as session:
            for plan in plans:
                p = await session.get(TradePlan, plan.id)
                if p:
                    p.confirm_status = "failed"
                    meta = dict(p.score_meta or {})
                    meta["confirm_skip_reason"] = "regime_defensive"
                    p.score_meta = meta
            await session.commit()
        return out

    # 3) ORB 평가 (각 watchlist 종목)
    passed: list[tuple[TradePlan, dict]] = []
    # (plan, reasons, eval_dict or None) — fail해도 ORB 데이터 저장
    failed_plans: list[tuple[TradePlan, list[str], dict | None]] = []

    for plan in plans:
        sym = plan.symbol.upper()
        today_bars, hist_bars = await _fetch_intraday_bars(sym)
        if today_bars is None or today_bars.empty:
            failed_plans.append((plan, ["no_intraday_data"], None))
            out["evaluations"].append({"symbol": sym, "status": "no_data"})
            continue

        # opening_range 모듈은 UTC index 기대 — convert back
        bars_utc = today_bars.copy()
        bars_utc.index = bars_utc.index.tz_convert("UTC")
        hist_utc = None
        if hist_bars is not None and not hist_bars.empty:
            hist_utc = hist_bars.copy()
            hist_utc.index = hist_utc.index.tz_convert("UTC")

        evaluation = evaluate_orb(
            sym, bars_utc, hist_utc, target,
        )
        if evaluation is None:
            failed_plans.append((plan, ["evaluation_none"], None))
            out["evaluations"].append({"symbol": sym, "status": "eval_none"})
            continue

        eval_dict = evaluation.to_dict()
        out["evaluations"].append({"symbol": sym, **eval_dict})

        if not evaluation.all_passed:
            failed_plans.append((plan, evaluation.fail_reasons, eval_dict))
            continue

        # ORB 기반 entry/stop/target
        levels = compute_entry_stop_target(
            evaluation, entry_offset=ENTRY_OFFSET
        )
        if levels is None:
            failed_plans.append((plan, ["invalid_r"], eval_dict))
            continue
        entry_v, stop_v, t1_v, t2_v = levels
        passed.append((plan, {
            "entry": entry_v, "stop": stop_v, "t1": t1_v, "t2": t2_v,
            "evaluation": eval_dict,
        }))

    # 4) 'failed' 상태 갱신 — ORB 데이터도 컬럼 + score_meta에 저장 (frontend 시각화용)
    from decimal import Decimal as _D
    async with async_session_factory() as session:
        for plan, reasons, eval_dict in failed_plans:
            p = await session.get(TradePlan, plan.id)
            if p is None:
                continue
            p.confirm_status = "failed"
            meta = dict(p.score_meta or {})
            meta["confirm_fail_reasons"] = reasons
            if eval_dict is not None:
                meta["orb_evaluation"] = eval_dict
                try:
                    p.orb_high = _D(f"{float(eval_dict['or_high']):.4f}")
                    p.orb_low = _D(f"{float(eval_dict['or_low']):.4f}")
                    p.session_vwap = _D(f"{float(eval_dict['session_vwap']):.4f}")
                    p.intraday_rvol = _D(f"{float(eval_dict['intraday_rvol']):.3f}")
                except Exception:
                    pass
            p.score_meta = meta
        await session.commit()
    out["failed_count"] = len(failed_plans)

    if not passed:
        out["status"] = "no_passed"
        logger.info("[confirm] no candidates passed ORB+VWAP+RVOL")
        return out

    # 5) Account / sizing
    adapter = get_adapter()
    try:
        acc = await adapter.get_account()
        out["account_equity"] = acc.equity
        out["buying_power"] = acc.buying_power

        if acc.trading_blocked:
            out["status"] = "blocked"
            out["reason"] = f"account trading blocked ({acc.account_id})"
            return out

        # Daily loss limit
        halt_pct = float(os.environ.get("DAILY_LOSS_HALT_PCT", "-3.0"))
        close_pct = float(os.environ.get("DAILY_LOSS_CLOSE_PCT", "-5.0"))
        auto_close = (
            os.environ.get("DAILY_LOSS_AUTO_CLOSE", "false").strip().lower() == "true"
        )
        loss_check = await _check_daily_loss_limit(
            adapter, acc,
            halt_pct=halt_pct, close_pct=close_pct, auto_close=auto_close,
        )
        out["daily_loss_check"] = loss_check
        if loss_check["status"] in ("close_all", "halt_new"):
            out["status"] = "blocked"
            out["reason"] = f"daily_loss_{loss_check['status']}"
            return out

        positions = await adapter.get_positions()
        held = {p.symbol for p in positions}
        open_orders = await adapter.get_orders(status="open")
        pending_count = Counter(o.symbol for o in open_orders)
        out["held_count"] = len(positions)

        # 6) ORB 기반 levels + sizing 으로 TradePlan 갱신 + bracket order 발송
        regime_mult = regime.position_size_multiplier()
        sector_cap = int(os.environ.get("SECTOR_CAP", "2"))
        sector_count: Counter[str] = Counter()
        position_cap = 5
        total_budget = 0.0

        # 점수순 정렬 (composite_score)
        passed.sort(key=lambda x: float(x[0].composite_score), reverse=True)
        passed = passed[:INTRADAY_PICK_CAP]  # 단타 top N

        async with async_session_factory() as session:
            for plan, info in passed:
                sym = plan.symbol.upper()
                entry = _penny(info["entry"])
                stop = _penny(info["stop"])
                t1 = _penny(info["t1"])
                t2 = _penny(info["t2"])
                r_per_share = entry - stop
                if r_per_share <= 0:
                    out["skipped"].append({"symbol": sym, "reason": "invalid_r_after_penny"})
                    continue
                if _penny(t1) == _penny(t2):
                    out["skipped"].append({"symbol": sym, "reason": "targets_equal_after_penny"})
                    continue

                # Sizing: equity × risk_pct / R, regime mult, gap penalty
                gap_pct = float(plan.premarket_gap_pct or 0.0)
                gp = _gap_penalty(gap_pct)
                if gp <= 0:
                    out["skipped"].append({"symbol": sym, "reason": f"gap_penalty_zero gap={gap_pct:.2f}%"})
                    continue
                base_shares = math.floor((acc.equity * INTRADAY_RISK_PCT) / r_per_share)
                qty = int(base_shares * regime_mult * gp)

                if qty <= 0:
                    out["skipped"].append({"symbol": sym, "reason": f"qty_zero base={base_shares} mult={regime_mult} gp={gp}"})
                    continue

                # 보유/pending 멱등성
                if sym in held:
                    out["skipped"].append({"symbol": sym, "reason": "already_held"})
                    continue
                if pending_count.get(sym, 0) >= 2:
                    out["skipped"].append({"symbol": sym, "reason": "pending_2_orders"})
                    continue
                if len(positions) + len([o for o in out["orders"]]) >= position_cap:
                    out["skipped"].append({"symbol": sym, "reason": f"position_cap {position_cap}"})
                    continue

                # Sector cap
                sector_key = (plan.sector or "_unk").strip().lower()
                if sector_count[sector_key] >= sector_cap:
                    out["skipped"].append({"symbol": sym, "reason": f"sector_cap {sector_key}"})
                    continue

                need = qty * entry
                if total_budget + need > acc.buying_power:
                    out["skipped"].append({
                        "symbol": sym,
                        "reason": f"insufficient_bp need=${need:,.0f}",
                    })
                    continue

                # Plan 갱신 (ORB levels + sizing 결과)
                amount_usd_eff = qty * entry
                eval_dict = info["evaluation"]
                p = await session.get(TradePlan, plan.id)
                if p is None:
                    continue
                p.entry_price = Decimal(f"{entry:.4f}")
                p.stop_price = Decimal(f"{stop:.4f}")
                p.target_1r = Decimal(f"{t1:.4f}")
                p.target_2r = Decimal(f"{t2:.4f}")
                p.shares = qty
                p.amount_usd = Decimal(f"{amount_usd_eff:.2f}")
                p.risk_usd = Decimal(f"{(qty * r_per_share):.2f}")
                p.orb_high = Decimal(f"{eval_dict['or_high']:.4f}")
                p.orb_low = Decimal(f"{eval_dict['or_low']:.4f}")
                p.session_vwap = Decimal(f"{eval_dict['session_vwap']:.4f}")
                p.intraday_rvol = Decimal(f"{eval_dict['intraday_rvol']:.3f}")
                meta = dict(p.score_meta or {})
                meta["orb_evaluation"] = eval_dict
                meta["sizing"] = {
                    "base_shares": base_shares,
                    "regime_mult": regime_mult,
                    "gap_penalty": gp,
                    "intraday_risk_pct": INTRADAY_RISK_PCT,
                }
                p.score_meta = meta

                # bracket order 발송 (or dry-run)
                if not auto_trade_enabled:
                    p.confirm_status = "passed"
                    await session.commit()
                    out["orders"].append({
                        "symbol": sym, "qty": qty, "amount_usd": round(amount_usd_eff, 2),
                        "entry": entry, "stop": stop, "t1": t1, "t2": t2,
                        "dry_run": True,
                    })
                    logger.info("[confirm-dryrun] %s qty=%d entry=$%.2f stop=$%.2f t1=$%.2f t2=$%.2f", sym, qty, entry, stop, t1, t2)
                    continue

                qty_1, qty_2 = qty_split_50_50(qty)
                is_two_tier = qty_2 > 0
                if is_two_tier:
                    req = BracketOrderRequest(
                        symbol=sym, qty=qty, side="BUY",
                        entry_type="stop_limit",
                        entry_price=entry, stop_loss_price=stop,
                        time_in_force="day", is_two_tier=True,
                        target_1_price=t1, target_1_qty=qty_1,
                        target_2_price=t2, target_2_qty=qty_2,
                    )
                else:
                    req = BracketOrderRequest(
                        symbol=sym, qty=qty, side="BUY",
                        entry_type="stop_limit",
                        entry_price=entry, stop_loss_price=stop,
                        take_profit_price=t1, time_in_force="day",
                    )

                try:
                    orders = await adapter.place_bracket_order(req)
                    total_budget += need
                    sector_count[sector_key] += 1
                    order_ids = [o.order_id for o in orders]
                    p.broker_order_ids = order_ids
                    p.confirm_status = "sent"
                    await session.commit()
                    out["orders"].append({
                        "symbol": sym, "qty": qty,
                        "qty_1r": qty_1, "qty_2r": qty_2,
                        "amount_usd": round(amount_usd_eff, 2),
                        "entry": entry, "stop": stop,
                        "target_1r": t1, "target_2r": t2,
                        "is_two_tier": is_two_tier,
                        "order_ids": order_ids,
                    })
                    logger.info(
                        "[confirm] SENT %s qty=%d (split %d+%d) entry=$%.2f stop=$%.2f t1=$%.2f t2=$%.2f",
                        sym, qty, qty_1, qty_2, entry, stop, t1, t2,
                    )
                except Exception as exc:
                    p.confirm_status = "skipped"
                    meta["order_error"] = str(exc)
                    p.score_meta = meta
                    await session.commit()
                    out["skipped"].append({"symbol": sym, "reason": f"order_fail: {exc}"})
                    logger.exception("place_bracket_order failed for %s", sym)

        out["total_budget_usd"] = round(total_budget, 2)
        out["sector_count"] = dict(sector_count)
        out["passed_count"] = len(passed)
        out["status"] = "ok"
    finally:
        await adapter.close()

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Intraday confirm phase (09:45 ET)")
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="대상 날짜 (기본 오늘)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="bracket order 실제 발송 X, 평가/계산만",
    )
    args = parser.parse_args()
    target = args.date or date.today()
    logger.info("=== intraday_confirm start target=%s dry_run=%s ===", target, args.dry_run)
    result = asyncio.run(run_confirm(target, dry_run=args.dry_run))
    logger.info("=== intraday_confirm done status=%s passed=%d orders=%d skipped=%d ===",
                result.get("status"), result.get("passed_count", 0),
                len(result.get("orders", [])), len(result.get("skipped", [])))


if __name__ == "__main__":
    main()
