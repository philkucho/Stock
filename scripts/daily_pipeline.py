"""Daily auto pipeline — 5-Model Intraday Stack 통합 실행.

매일 5단계로 실행 (Windows Task Scheduler 권장):
  AM 09:00 ET: --phase log       (당일 4 시스템 picks 적재 v3/scanner/v10/dashboard)
  AM 09:25 ET: --phase preopen   (5-Model intraday watchlist top 5 → trade_plans, dispatch_mode=orb_auto)
  AM 09:30 ET: --phase trade     (dispatch_mode=user_fixed plan을 사용자 입력값 그대로 bracket 발송)
  AM 09:45 ET: --phase confirm   (dispatch_mode=orb_auto plan에 ORB+VWAP+RVOL → top 3 bracket 발송)
  PM 11:30 ET: --phase monitor   (1차 hit 후 stop breakeven 갱신; 미체결 cancel)
  PM 16:30 ET: --phase backfill  (1d/5d/10d outcome 적재)

CLI:
  python -m scripts.daily_pipeline --phase log
  python -m scripts.daily_pipeline --phase preopen
  python -m scripts.daily_pipeline --phase confirm
  python -m scripts.daily_pipeline --phase backfill
  python -m scripts.daily_pipeline --date 2026-05-10
  python -m scripts.daily_pipeline --lookback 60

`trade` phase는 legacy (v10 직접 발송) — preopen+confirm 으로 대체.

Confirm phase 안전장치:
  1. AUTO_TRADE_ENABLED=false → dry-run only
  2. Regime defensive (long_blocked) → 차단
  3. ORB+VWAP+RVOL 4-pass 실패 종목 → confirm_status='failed' (발송 X)
  4. 동일 symbol 보유 → skip
  5. position cap 5종목 → 차단
  6. account.trading_blocked → 차단
  7. daily_loss_halt/close → 차단

Windows Task Scheduler:
  .\scripts\install_daily_pipeline_task.ps1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "daily_pipeline"
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

logger = logging.getLogger("daily_pipeline")


async def run_log(target: date) -> dict:
    """3 시스템 picks 적재 (v3 / scanner / integrated v10)."""
    from api.db import async_session_factory
    from scanner.comparison.logger import log_daily_picks

    async with async_session_factory() as session:
        result = await log_daily_picks(session, target)
    logger.info("[log] picks logged: %s", result)
    return result


async def run_backfill(target: date, lookback: int = 30) -> dict:
    """1d/5d/10d outcome backfill + reconciliation.

    - pick_outcomes: 3 시스템 비교용 (system_pick_logs 기반)
    - trade_plan_outcomes: 사용자 매매 plan + 2-tier 부분 청산 결과
    - reconciliation: Alpaca positions vs DB plans 불일치 alert
    """
    from api.db import async_session_factory
    from broker_adapter import get_adapter
    from notifications import reconcile_broker_state, send_heartbeat
    from scanner.comparison.outcomes import (
        backfill_pick_outcomes,
        backfill_trade_plan_outcomes,
    )

    out: dict = {}
    async with async_session_factory() as session:
        out["pick_outcomes"] = await backfill_pick_outcomes(
            session, target, lookback_days=lookback
        )
    logger.info("[backfill] pick_outcomes: %s", out["pick_outcomes"])

    async with async_session_factory() as session:
        out["trade_plan_outcomes"] = await backfill_trade_plan_outcomes(
            session, target, lookback_days=lookback
        )
    logger.info("[backfill] trade_plan_outcomes: %s", out["trade_plan_outcomes"])

    # Reconciliation (broker drift 감지)
    try:
        adapter = get_adapter()
        try:
            recon = await reconcile_broker_state(adapter, target)
            out["reconciliation"] = recon
            if recon["discrepancies"]:
                send_heartbeat(
                    phase="backfill",
                    status="alert",
                    message=f"Broker drift detected: {len(recon['discrepancies'])} discrepancies",
                    details=recon,
                )
                logger.warning("[backfill] reconciliation found %d drifts", len(recon["discrepancies"]))
            else:
                logger.info("[backfill] reconciliation clean: %d positions, %d plans", recon["broker_positions"], recon["db_plans"])
        finally:
            await adapter.close()
    except Exception as exc:
        out["reconciliation_error"] = str(exc)
        logger.warning("[backfill] reconciliation failed: %s", exc)

    return out


async def _sync_fill_progress(adapter, plan) -> dict | None:
    """2.2 부분 청산 + 1.1 entry partial 동기화.

    각 bracket(broker_order_ids[0/1])에 대해:
      - parent.filled_qty → entry_filled_qty_1/2 (BUY entry leg)
      - children take_profit leg.filled_qty → filled_qty_1r/2r (SELL 익절 leg)

    expected_holding = (entry_filled_1 + entry_filled_2) - (filled_qty_1r + filled_qty_2r)
    이 값이 broker holding과 다르면 reconcile에서 mismatch_qty alert.

    monitor가 매 15분마다 호출. 변경 없으면 commit skip.
    """
    from api.db.models import TradePlan
    from api.db.session import async_session_factory

    if not plan.broker_order_ids or len(plan.broker_order_ids) < 2:
        return None

    try:
        # parent 자체 (entry BUY leg fill)
        parent_1 = await adapter.get_order(plan.broker_order_ids[0])
        parent_2 = await adapter.get_order(plan.broker_order_ids[1])
        # children (stop_loss + take_profit)
        children_1 = await adapter.get_order_children(plan.broker_order_ids[0])
        children_2 = await adapter.get_order_children(plan.broker_order_ids[1])
    except Exception as exc:
        logger.warning("[fill_sync] %s broker query failed: %s", plan.symbol, exc)
        return None

    def _tp_leg(children):
        for c in children:
            if "limit" in c.order_type.lower() and c.side == "SELL":
                return c
        return None

    # ENTRY parent fill (1.1)
    eq_1 = parent_1.filled_qty if parent_1 else 0
    eq_2 = parent_2.filled_qty if parent_2 else 0

    # SELL leg fill (2.2)
    leg_1 = _tp_leg(children_1)
    leg_2 = _tp_leg(children_2)
    fq_1 = leg_1.filled_qty if leg_1 else 0
    fq_2 = leg_2.filled_qty if leg_2 else 0
    fp_1 = leg_1.filled_avg_price if leg_1 else None
    fp_2 = leg_2.filled_avg_price if leg_2 else None

    cur_1 = plan.filled_qty_1r or 0
    cur_2 = plan.filled_qty_2r or 0
    cur_e1 = plan.entry_filled_qty_1 or 0
    cur_e2 = plan.entry_filled_qty_2 or 0
    if fq_1 == cur_1 and fq_2 == cur_2 and eq_1 == cur_e1 and eq_2 == cur_e2:
        return None  # 변경 없음

    from decimal import Decimal as _D
    async with async_session_factory() as s:
        p = await s.get(TradePlan, plan.id)
        if p is None:
            return None
        p.filled_qty_1r = fq_1
        p.filled_qty_2r = fq_2
        p.entry_filled_qty_1 = eq_1
        p.entry_filled_qty_2 = eq_2
        if fp_1 is not None:
            p.filled_avg_price_1r = _D(f"{fp_1:.4f}")
        if fp_2 is not None:
            p.filled_avg_price_2r = _D(f"{fp_2:.4f}")
        await s.commit()

    # 1.1 Entry partial 감지: parent.filled_qty < parent.qty이면 부분 체결.
    entry_partial_1 = parent_1 and parent_1.qty > 0 and eq_1 < parent_1.qty
    entry_partial_2 = parent_2 and parent_2.qty > 0 and eq_2 < parent_2.qty
    if entry_partial_1 or entry_partial_2:
        logger.warning(
            "[fill_sync] %s ENTRY PARTIAL: 1차 %d/%d, 2차 %d/%d — over-sell 위험. reconcile 확인 필요.",
            plan.symbol,
            eq_1, parent_1.qty if parent_1 else 0,
            eq_2, parent_2.qty if parent_2 else 0,
        )

    logger.info(
        "[fill_sync] %s entry:%d+%d sell:%d+%d (was sell:%d+%d entry:%d+%d)",
        plan.symbol, eq_1, eq_2, fq_1, fq_2, cur_1, cur_2, cur_e1, cur_e2,
    )
    return {
        "symbol": plan.symbol,
        "entry_filled": [eq_1, eq_2],
        "sell_filled": [fq_1, fq_2],
        "entry_partial": bool(entry_partial_1 or entry_partial_2),
    }


async def run_monitor(target: date | None = None) -> dict:
    """장중 모니터 — 매 1시간 실행 (10:00 ~ 15:00 ET).

    수행 작업 (순서):
      1. Daily loss 장중 체크 — close_pct 도달 시 즉시 close_all (auto_close=true), halt_new는 logging
      2. Reconciliation — broker positions/orders vs DB plan 비교, drift 시 alert
      3. Per-plan: 1차 target 도달 시 2차 bracket의 stop을 entry로 raise (breakeven trailing)
         - 가격 fetch 시 staleness 가드 (30분 초과 시 skip)
         - 멱등성: trade_plans.score_meta['stop_raised_to_breakeven']

    안전장치:
      - 2-tier plan만 stop raise 처리 (broker_order_ids 길이 2)
      - 이미 갱신한 plan skip
      - yfinance staleness 30분 초과 시 skip
    """
    import os

    import yfinance as yf
    from sqlalchemy import select

    from api.db.models import TradePlan
    from api.db.session import async_session_factory
    from broker_adapter import get_adapter
    from notifications import reconcile_broker_state, send_heartbeat

    if target is None:
        target = date.today()

    out: dict = {
        "date": target.isoformat(),
        "auto_trade_enabled": os.environ.get("AUTO_TRADE_ENABLED", "false").lower() == "true",
        "raised": [],
        "skipped": [],
        "fill_synced": [],
    }

    adapter = get_adapter()
    try:
        # --- (1) Daily loss 장중 체크 ---
        try:
            acc = await adapter.get_account()
            halt_pct = float(os.environ.get("DAILY_LOSS_HALT_PCT", "-3.0"))
            close_pct = float(os.environ.get("DAILY_LOSS_CLOSE_PCT", "-5.0"))
            auto_close = os.environ.get("DAILY_LOSS_AUTO_CLOSE", "false").strip().lower() == "true"
            loss_check = await _check_daily_loss_limit(
                adapter, acc,
                halt_pct=halt_pct, close_pct=close_pct, auto_close=auto_close,
            )
            out["daily_loss_check"] = loss_check
            if loss_check["status"] == "close_all":
                send_heartbeat(
                    phase="monitor",
                    status="alert",
                    message=f"Daily loss CLOSE breach: PnL {loss_check['daily_pnl_pct']}% <= {close_pct}%",
                    details=loss_check,
                )
                # close_all일 땐 stop raise 의미 없음 — 즉시 종료
                out["status"] = "blocked"
                out["reason"] = f"daily_loss_close_all (auto_close={auto_close})"
                return out
            if loss_check["status"] == "halt_new":
                # monitor는 신규 발송 안 하므로 logging only — stop raise는 계속 진행
                logger.warning(
                    "[monitor] daily_loss halt_new (PnL %.2f%%) — 신규 진입 차단 중. stop raise는 계속.",
                    loss_check["daily_pnl_pct"],
                )
        except Exception as exc:
            logger.warning("[monitor] daily_loss_check failed (계속 진행): %s", exc)
            out["daily_loss_check_error"] = str(exc)

        # --- (2) Reconciliation ---
        try:
            recon = await reconcile_broker_state(adapter, target)
            out["reconciliation"] = recon
            if recon["discrepancies"]:
                logger.warning(
                    "[monitor] broker drift: %d discrepancies — %s",
                    len(recon["discrepancies"]),
                    [d["type"] for d in recon["discrepancies"]],
                )
                send_heartbeat(
                    phase="monitor",
                    status="alert",
                    message=f"Broker drift: {len(recon['discrepancies'])} discrepancies",
                    details=recon,
                )
        except Exception as exc:
            logger.warning("[monitor] reconciliation failed (계속 진행): %s", exc)
            out["reconciliation_error"] = str(exc)

        # --- (2.5) 보호 stop 자동 재발송 — 보유 포지션 중 SELL stop이 없는 종목 감지·발송 ---
        # (2026-05-14 사고 follow-up: bracket 자식이 broker 측에서 사라져도 monitor가 복구)
        try:
            protect_result = await _ensure_protective_stops(adapter, target)
            out["protective_stops"] = protect_result
            if protect_result.get("added"):
                send_heartbeat(
                    phase="monitor",
                    status="alert",
                    message=f"🛡 보호 stop 자동 재발송: {len(protect_result['added'])}건",
                    details=protect_result,
                )
        except Exception as exc:
            logger.warning("[monitor] protective_stops failed (계속 진행): %s", exc)
            out["protective_stops_error"] = str(exc)

        # --- (3) Per-plan: 1차 hit → 2차 stop raise ---
        async with async_session_factory() as session:
            stmt = (
                select(TradePlan)
                .where(TradePlan.plan_date == target)
                .order_by(TradePlan.rank)
            )
            plans = list((await session.execute(stmt)).scalars().all())

        out["plans_count"] = len(plans)
        if not plans:
            out["status"] = "no_plans"
            return out

        for plan in plans:
            sym = plan.symbol.upper()

            # 3.2 Trading halt 감지 — broker가 보고하는 종목 거래 가능 여부 확인.
            # halt면 plan의 모든 pending order cancel + alert. broker_order_ids 발송한 plan만 의미 있음.
            if plan.broker_order_ids:
                try:
                    asset = await adapter.get_asset_info(sym)
                    if asset.is_halted_or_inactive:
                        cancel_count = 0
                        for oid in plan.broker_order_ids:
                            try:
                                if await adapter.cancel_order(oid):
                                    cancel_count += 1
                            except Exception as cancel_exc:
                                logger.warning("[monitor halt] cancel %s failed: %s", oid[:12], cancel_exc)
                        msg = (
                            f"HALT: {sym} tradable={asset.tradable} status={asset.status} "
                            f"— cancelled {cancel_count}/{len(plan.broker_order_ids)} orders"
                        )
                        logger.error("[monitor] %s", msg)
                        send_heartbeat(
                            phase="monitor", status="alert",
                            message=msg,
                            details={
                                "symbol": sym,
                                "tradable": asset.tradable,
                                "status": asset.status,
                                "cancelled_orders": cancel_count,
                                "total_orders": len(plan.broker_order_ids),
                            },
                        )
                        out["skipped"].append({
                            "symbol": sym,
                            "reason": f"halted (tradable={asset.tradable}, status={asset.status}); cancelled {cancel_count} orders",
                        })
                        continue
                except Exception as exc:
                    # halt 체크 실패해도 stop raise 차단할 이유는 없음 — log only
                    logger.warning("[monitor] halt check failed for %s: %s", sym, exc)

            if not plan.broker_order_ids or len(plan.broker_order_ids) < 2:
                out["skipped"].append({"symbol": sym, "reason": "not_two_tier"})
                continue

            # 2.2 부분 청산 진행도 동기화 — children fill 정보 → DB 갱신
            sync_result = await _sync_fill_progress(adapter, plan)
            if sync_result is not None:
                out["fill_synced"].append(sync_result)

            meta = plan.score_meta or {}
            if meta.get("stop_raised_to_breakeven"):
                out["skipped"].append({"symbol": sym, "reason": "already_raised"})
                continue

            target_1r = float(plan.target_1r)
            entry = float(plan.entry_price)

            # 일중 high 조회 (yfinance 1m 또는 1d)
            try:
                hist = yf.download(sym, period="1d", interval="1m", progress=False, auto_adjust=False)
                if hist is None or hist.empty:
                    out["skipped"].append({"symbol": sym, "reason": "no_intraday_data"})
                    continue
                import pandas as pd
                if isinstance(hist.columns, pd.MultiIndex):
                    hist.columns = hist.columns.get_level_values(0)
                hist.columns = [c.lower() for c in hist.columns]

                # 4.2 Staleness 가드: 마지막 bar가 N분 이상 오래된 경우 skip.
                # 시간외/장 종료 후엔 자연스럽게 stale → skip이 정상 동작.
                from datetime import timezone as _tz
                last_bar_ts = hist.index.max()
                if last_bar_ts.tzinfo is None:
                    last_bar_ts = last_bar_ts.tz_localize("UTC")
                age_min = (datetime.now(_tz.utc) - last_bar_ts.to_pydatetime()).total_seconds() / 60
                staleness_max_min = float(os.environ.get("YFINANCE_STALENESS_MAX_MIN", "30"))
                if age_min > staleness_max_min:
                    out["skipped"].append({
                        "symbol": sym,
                        "reason": f"stale_data age={age_min:.0f}min > {staleness_max_min:.0f}min (last bar {last_bar_ts.isoformat()})",
                    })
                    continue

                today_high = float(hist["high"].max())
            except Exception as exc:
                out["skipped"].append({"symbol": sym, "reason": f"price_fetch_fail: {exc}"})
                continue

            if today_high < target_1r:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"target_1r_not_reached high=${today_high:.2f} < t1=${target_1r:.2f}",
                })
                continue

            # 1차 hit 확인 — Order 2 (broker_order_ids[1])의 stop leg 갱신
            order_2_parent_id = plan.broker_order_ids[1]
            children = await adapter.get_order_children(order_2_parent_id)
            stop_leg = next(
                (c for c in children if "stop" in c.order_type.lower()), None
            )
            if not stop_leg:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"order_2_no_stop_leg parent={order_2_parent_id[:12]}",
                })
                continue

            # 이미 entry 이상으로 갱신됐으면 skip (멱등 broker side)
            if stop_leg.raw and stop_leg.raw.get("stop_price"):
                cur_stop = float(stop_leg.raw["stop_price"])
                if cur_stop >= entry:
                    out["skipped"].append({
                        "symbol": sym,
                        "reason": f"already_at_breakeven cur_stop=${cur_stop:.2f}",
                    })
                    continue

            success = await adapter.replace_order_stop(stop_leg.order_id, entry)
            if success:
                # plan에 flag 저장 (멱등)
                async with async_session_factory() as s2:
                    p2 = await s2.get(TradePlan, plan.id)
                    if p2:
                        new_meta = dict(p2.score_meta or {})
                        new_meta["stop_raised_to_breakeven"] = True
                        new_meta["stop_raised_at"] = datetime.now().isoformat()
                        new_meta["stop_raised_to"] = entry
                        p2.score_meta = new_meta
                        await s2.commit()
                out["raised"].append({
                    "symbol": sym,
                    "entry": entry,
                    "old_stop": float(plan.stop_price),
                    "new_stop": entry,
                    "today_high": today_high,
                    "target_1r": target_1r,
                    "stop_leg_id": stop_leg.order_id,
                })
                logger.info(
                    "[monitor] %s 1차 hit (high=$%.2f >= t1=$%.2f) → 잔여 stop $%.2f → $%.2f (breakeven)",
                    sym, today_high, target_1r, float(plan.stop_price), entry,
                )
            else:
                out["skipped"].append({"symbol": sym, "reason": "replace_order_failed"})

        out["status"] = "ok"
    finally:
        await adapter.close()

    return out


async def _check_daily_loss_limit(
    adapter,
    account,
    *,
    halt_pct: float = -3.0,
    close_pct: float = -5.0,
    auto_close: bool = False,
) -> dict:
    """Daily loss circuit breaker.

    halt_pct (-3% 기본): 도달 시 신규 진입 차단 (기존 OCO bracket은 유지 → broker side stop 작동)
    close_pct (-5% 기본): alert 발송. auto_close=True면 모든 open orders cancel + positions 강제 close

    반환: {
      "status": "ok" | "halt_new" | "close_all",
      "daily_pnl_pct": float,
      "last_equity": float, "cur_equity": float,
      "actions": list[str]  # auto_close 발동 시 수행한 동작
    }
    """
    daily_pnl_pct = account.daily_pnl_pct
    info = {
        "status": "ok",
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "last_equity": account.last_equity,
        "cur_equity": account.equity,
        "halt_pct": halt_pct,
        "close_pct": close_pct,
        "actions": [],
    }

    if daily_pnl_pct <= close_pct:
        info["status"] = "close_all"
        logger.error(
            "[DAILY LOSS] CRITICAL: daily PnL %.2f%% <= %.2f%% — close_pct breach",
            daily_pnl_pct, close_pct,
        )
        if auto_close:
            try:
                open_orders = await adapter.get_orders(status="open")
                for o in open_orders:
                    await adapter.cancel_order(o.order_id)
                info["actions"].append(f"cancelled {len(open_orders)} open orders")

                positions = await adapter.get_positions()
                for p in positions:
                    await adapter.close_position(p.symbol)
                info["actions"].append(f"closed {len(positions)} positions")
                logger.error(
                    "[DAILY LOSS] AUTO_CLOSE executed: %d orders cancelled, %d positions closed",
                    len(open_orders), len(positions),
                )
            except Exception as exc:
                info["actions"].append(f"auto_close_error: {exc}")
                logger.exception("[DAILY LOSS] auto_close failed")
        else:
            info["actions"].append("alert_only (DAILY_LOSS_AUTO_CLOSE=false)")
        return info

    if daily_pnl_pct <= halt_pct:
        info["status"] = "halt_new"
        logger.warning(
            "[DAILY LOSS] HALT: daily PnL %.2f%% <= %.2f%% — 신규 진입 차단 (기존 OCO 유지)",
            daily_pnl_pct, halt_pct,
        )
        return info

    return info


async def run_preopen(target: date) -> dict:
    """Phase 4: Preopen (09:25 ET) — 5-Model Intraday Stack 으로 watchlist 산출.

    1. v10 + 운영 v3 + catalyst + regime 통합 (`run_integrated_intraday`)
    2. Top 5 watchlist를 trade_plans 테이블에 upsert (confirm_status='watchlist')
    3. provisional entry/stop/target은 score_meta에서 복사

    실제 ORB-based 가격/sizing은 Phase 5 (09:45 confirm) 에서 덮어씀.
    """
    from decimal import Decimal

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from api.db import async_session_factory
    from api.db.models import TradePlan
    from scanner.integrated.run import run_integrated_intraday

    out: dict = {"date": target.isoformat(), "watchlist": [], "skipped": []}

    async with async_session_factory() as session:
        picks = await run_integrated_intraday(target, top=5, session=session)

    out["pick_count"] = len(picks)
    if not picks:
        out["status"] = "no_picks"
        logger.warning("[preopen] no intraday picks for %s", target)
        return out

    rows: list[dict] = []
    for p in picks:
        meta = p.score_meta or {}
        entry = float(meta.get("provisional_entry") or 0.0)
        stop = float(meta.get("provisional_stop") or 0.0)
        t1 = float(meta.get("provisional_target_1r") or 0.0)
        t2 = float(meta.get("provisional_target_2r") or 0.0)
        if entry <= 0 or stop <= 0 or t1 <= 0 or t2 <= 0:
            out["skipped"].append({"symbol": p.symbol, "reason": "missing_provisional_levels"})
            continue
        risk_per_share = entry - stop
        if risk_per_share <= 0:
            out["skipped"].append({"symbol": p.symbol, "reason": "invalid_provisional_r"})
            continue

        # Placeholder shares/amount — Phase 5 가 실제 sizing 으로 덮어씀
        provisional_shares = 1
        provisional_amount = entry * provisional_shares

        rows.append({
            "plan_date": target,
            "symbol": p.symbol.upper(),
            "rank": p.rank,
            "amount_usd": Decimal(f"{provisional_amount:.2f}"),
            "entry_price": Decimal(f"{entry:.4f}"),
            "stop_price": Decimal(f"{stop:.4f}"),
            "target_1r": Decimal(f"{t1:.4f}"),
            "target_2r": Decimal(f"{t2:.4f}"),
            "composite_score": Decimal(f"{float(p.score):.2f}"),
            "score_meta": meta,
            "sector": p.sector,
            "shares": provisional_shares,
            "risk_usd": Decimal(f"{(provisional_shares * risk_per_share):.2f}"),
            "premarket_gap_pct": Decimal(f"{float(meta.get('premarket_gap_pct', 0) or 0):.3f}"),
            "premarket_rvol": Decimal(f"{float(meta.get('premarket_rvol', 0) or 0):.3f}"),
            "confirm_status": "watchlist",
            "dispatch_mode": "orb_auto",
        })

    if not rows:
        out["status"] = "no_valid_picks"
        return out

    async with async_session_factory() as session:
        stmt = pg_insert(TradePlan).values(rows)
        # 같은 (plan_date, symbol)에 사용자가 손댄 plan이 있을 수 있음 — confirm_status로 보호
        update_cols = {
            "rank": stmt.excluded.rank,
            "entry_price": stmt.excluded.entry_price,
            "stop_price": stmt.excluded.stop_price,
            "target_1r": stmt.excluded.target_1r,
            "target_2r": stmt.excluded.target_2r,
            "composite_score": stmt.excluded.composite_score,
            "score_meta": stmt.excluded.score_meta,
            "sector": stmt.excluded.sector,
            "premarket_gap_pct": stmt.excluded.premarket_gap_pct,
            "premarket_rvol": stmt.excluded.premarket_rvol,
            "confirm_status": stmt.excluded.confirm_status,
        }
        # 보호 조건:
        # 1) confirm_status='sent': 이미 발송된 plan은 덮어쓰지 않음
        # 2) dispatch_mode='user_fixed': 사용자 직접 입력은 스캐너 자동 watchlist가 덮어쓰지 못함
        stmt = stmt.on_conflict_do_update(
            constraint="uq_trade_plan_date_sym",
            set_=update_cols,
            where=(
                (TradePlan.confirm_status != "sent")
                & (TradePlan.dispatch_mode != "user_fixed")
            ),
        )
        await session.execute(stmt)
        await session.commit()

    out["watchlist"] = [
        {"symbol": r["symbol"], "rank": r["rank"], "score": float(r["composite_score"])}
        for r in rows
    ]

    # ── AI advisor morning brief (best-effort) ──
    # ADVISOR_ENABLED=true일 때만 실제 Claude 호출. 실패해도 preopen은 ok 반환.
    import os as _os

    if _os.environ.get("ADVISOR_ENABLED", "false").strip().lower() == "true":
        try:
            from services.advisor.service import run_morning_brief

            async with async_session_factory() as s:
                advisor_result = await run_morning_brief(s, target)
            out["advisor"] = {
                "status": advisor_result.get("status"),
                "created_count": advisor_result.get("created_count", 0),
                "skipped": len(advisor_result.get("skipped", [])),
                "market_summary": advisor_result.get("market_summary"),
            }
            logger.info(
                "[preopen] advisor: %s, created=%d",
                advisor_result.get("status"),
                advisor_result.get("created_count", 0),
            )
        except Exception as exc:
            logger.warning("[preopen] advisor failed (계속 진행): %s", exc)
            out["advisor_error"] = str(exc)

    out["status"] = "ok"
    logger.info("[preopen] watchlist (%d): %s", len(rows), [r["symbol"] for r in rows])
    return out


async def run_confirm_phase(target: date) -> dict:
    """Phase 5: Confirm (09:45 ET) — ORB+VWAP+RVOL 평가 → 통과한 top N bracket 발송.

    `scripts/intraday_confirm.run_confirm`을 호출하는 thin wrapper.
    """
    from scripts.intraday_confirm import run_confirm
    return await run_confirm(target)


async def run_trade(target: date, *, position_cap: int = 5) -> dict:
    """trade_plans (사용자가 매일 아침 입력) → bracket orders 발송.

    사용자 워크플로우:
      1) 매일 아침 /trading 페이지에서 종목별 amount_usd 입력
         → trade_plans 테이블에 저장 (entry_price/stop_price/target_1r 자동 산출)
      2) 09:25 ET cron이 이 함수 호출 → 입력된 plan 기반 bracket order 발송
      3) 09:30 개장 시 entry 가격 도달하면 자동 체결, OCO bracket 활성화

    Order 형식: stop-limit entry @ entry_price, OCO bracket (stop_loss + target_1r).
    Position size: plan.shares (사용자 amount_usd / entry_price 자동 계산).

    안전장치:
      - AUTO_TRADE_ENABLED=false → dry-run
      - regime defensive → 차단 (사용자가 입력했어도 추가 안전장치)
      - 동일 symbol 보유/pending → skip (reentry 방지)
      - position cap 5종목 초과 → 차단
    """
    import os
    from collections import Counter

    from sqlalchemy import select

    from api.db.models import TradePlan
    from api.db.session import async_session_factory
    from broker_adapter import get_adapter
    from broker_adapter.alpaca_adapter import _penny
    from broker_adapter.base import BracketOrderRequest, qty_split_50_50
    from scanner.regime import evaluate_regime

    out: dict = {
        "date": target.isoformat(),
        "orders": [],
        "skipped": [],
        "auto_trade_enabled": os.environ.get("AUTO_TRADE_ENABLED", "false").lower() == "true",
    }

    # 0) advisor 만료 정리 — TTL 경과 pending → 'expired' (절대 자동 실행 X 원칙)
    try:
        from services.advisor.service import expire_overdue_recommendations
        async with async_session_factory() as _s_exp:
            expired_count = await expire_overdue_recommendations(_s_exp)
        if expired_count:
            out["advisor_expired"] = expired_count
            logger.info("[trade] advisor: %d recommendations expired", expired_count)
    except Exception as exc:
        logger.warning("[trade] advisor expire failed: %s", exc)

    # 1) 사용자 입력 plans 조회 (dispatch_mode='user_fixed'만 — orb_auto는 09:45 confirm이 처리)
    async with async_session_factory() as session:
        stmt = (
            select(TradePlan)
            .where(TradePlan.plan_date == target)
            .where(TradePlan.dispatch_mode == "user_fixed")
            .order_by(TradePlan.rank)
        )
        plans = list((await session.execute(stmt)).scalars().all())

    out["plans_count"] = len(plans)
    if not plans:
        out["status"] = "no_plans"
        out["reason"] = "사용자가 오늘 trade plan을 입력하지 않음 (user_fixed)"
        logger.info("[trade] no user_fixed plans for %s — 사용자 입력 대기", target)
        return out

    # 2) Regime check (사용자 입력 plan이 있어도 방어모드면 차단)
    regime = evaluate_regime(target)
    out["regime_score"] = regime.score
    out["regime_mode"] = regime.mode
    if regime.long_blocked():
        out["status"] = "blocked"
        out["reason"] = f"regime defensive (score={regime.score:.0f}/15) — 사용자 plan 무시 차단"
        logger.warning("[trade] BLOCKED: %s", out["reason"])
        return out

    # 3) Adapter
    adapter = get_adapter()
    try:
        acc = await adapter.get_account()
        out["account_id"] = acc.account_id
        out["account_equity"] = acc.equity
        out["account_last_equity"] = acc.last_equity
        out["buying_power"] = acc.buying_power

        if acc.trading_blocked:
            out["status"] = "blocked"
            out["reason"] = f"account trading blocked ({acc.account_id})"
            return out

        # 3-bis) Daily loss limit (circuit breaker)
        halt_pct = float(os.environ.get("DAILY_LOSS_HALT_PCT", "-3.0"))
        close_pct = float(os.environ.get("DAILY_LOSS_CLOSE_PCT", "-5.0"))
        auto_close = os.environ.get("DAILY_LOSS_AUTO_CLOSE", "false").strip().lower() == "true"
        loss_check = await _check_daily_loss_limit(
            adapter, acc,
            halt_pct=halt_pct, close_pct=close_pct, auto_close=auto_close,
        )
        out["daily_loss_check"] = loss_check
        if loss_check["status"] == "close_all":
            out["status"] = "blocked"
            out["reason"] = (
                f"daily_loss_close (PnL {loss_check['daily_pnl_pct']:.2f}% <= {close_pct}%) "
                f"actions={loss_check['actions']}"
            )
            return out
        if loss_check["status"] == "halt_new":
            out["status"] = "blocked"
            out["reason"] = (
                f"daily_loss_halt (PnL {loss_check['daily_pnl_pct']:.2f}% <= {halt_pct}%) "
                "— 기존 포지션 유지, 신규 진입 차단"
            )
            return out

        positions = await adapter.get_positions()
        held = {p.symbol for p in positions}
        open_orders = await adapter.get_orders(status="open")
        pending_count: Counter[str] = Counter(o.symbol for o in open_orders)
        out["held_count"] = len(positions)
        out["pending_count"] = len(open_orders)

        if len(positions) >= position_cap:
            out["status"] = "blocked"
            out["reason"] = f"position cap reached ({len(positions)}/{position_cap})"
            return out

        # 4) 발송 (2-tier 부분 청산)
        total_budget = 0.0
        sector_cap = int(os.environ.get("SECTOR_CAP", "2"))
        sector_count: Counter[str] = Counter()
        out["sector_cap"] = sector_cap
        for plan in plans:
            sym = plan.symbol.upper()

            # 멱등성: 이미 발송된 plan
            if plan.broker_order_ids:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"already_sent broker_order_ids={plan.broker_order_ids}",
                })
                continue

            # Reentry 방지
            if sym in held:
                out["skipped"].append({"symbol": sym, "reason": "already_held"})
                continue
            # 2-tier는 symbol당 최대 2개 pending 허용
            if pending_count.get(sym, 0) >= 2:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"pending_2_orders ({pending_count[sym]} pending)",
                })
                continue

            # Position cap 점진 체크 — 보유 + 발송 누적이 cap 도달 시 skip.
            # 시작 시점 체크(line 568-574)는 fail-fast용 보조, 본 체크가 실효 제약.
            if len(positions) + len(out["orders"]) >= position_cap:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": (
                        f"position_cap_reached "
                        f"({len(positions)} held + {len(out['orders'])} sent >= {position_cap})"
                    ),
                })
                continue

            # Sector concentration cap (오늘 plan들 사이에서)
            sector_key = (plan.sector or "_unknown").strip().lower()
            if sector_count[sector_key] >= sector_cap:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"sector_cap (sector={plan.sector!r}, already={sector_count[sector_key]}, cap={sector_cap})",
                })
                continue

            entry = float(plan.entry_price)
            stop = float(plan.stop_price)
            t1 = float(plan.target_1r)
            t2 = float(plan.target_2r)
            qty = int(plan.shares)
            amount = float(plan.amount_usd)

            if entry <= 0 or stop <= 0 or t1 <= 0 or t2 <= 0 or stop >= entry or t1 <= entry:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"invalid_prices entry=${entry:.2f} stop=${stop:.2f} t1=${t1:.2f} t2=${t2:.2f}",
                })
                continue
            if t2 <= t1:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"targets_inverted t1=${t1:.2f} >= t2=${t2:.2f}",
                })
                continue
            # penny 반올림 충돌
            if _penny(t1) == _penny(t2):
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"targets_equal_after_penny t1=t2=${_penny(t1)}",
                })
                continue
            if qty <= 0:
                out["skipped"].append({"symbol": sym, "reason": "qty_zero"})
                continue

            # buying_power 검증 (1차+2차 합산은 qty와 동일 — entry는 한 번만 체결)
            need = qty * entry
            if total_budget + need > acc.buying_power:
                out["skipped"].append({
                    "symbol": sym,
                    "reason": f"insufficient_buying_power need=${need:,.0f} remaining=${acc.buying_power - total_budget:,.0f}",
                })
                continue

            # 2-tier 분할
            qty_1, qty_2 = qty_split_50_50(qty)
            is_two_tier = qty_2 > 0  # qty=1이면 1-tier fallback

            if is_two_tier:
                req = BracketOrderRequest(
                    symbol=sym,
                    qty=qty,
                    side="BUY",
                    entry_type="stop_limit",
                    entry_price=entry,
                    stop_loss_price=stop,
                    # GTC: parent+child가 마감 후에도 살아있어야 stop/tp 보호가 유지됨.
                    # (DAY는 자식이 9:35경 expire/cancel되는 사고가 발생 — 2026-05-14 사고)
                    time_in_force="gtc",
                    is_two_tier=True,
                    target_1_price=t1, target_1_qty=qty_1,
                    target_2_price=t2, target_2_qty=qty_2,
                )
            else:
                # qty=1 fallback: 단일 BRACKET (target_1r만)
                req = BracketOrderRequest(
                    symbol=sym,
                    qty=qty,
                    side="BUY",
                    entry_type="stop_limit",
                    entry_price=entry,
                    stop_loss_price=stop,
                    take_profit_price=t1,
                    time_in_force="gtc",
                )

            try:
                orders = await adapter.place_bracket_order(req)
                total_budget += need
                sector_count[sector_key] += 1  # 발송 성공 후 sector counter 증가
                order_ids = [o.order_id for o in orders]
                # plan에 broker_order_ids 저장 (멱등)
                async with async_session_factory() as s2:
                    p2 = await s2.get(TradePlan, plan.id)
                    if p2:
                        p2.broker_order_ids = order_ids
                        await s2.commit()
                out["orders"].append({
                    "symbol": sym,
                    "qty": qty,
                    "qty_1r": qty_1, "qty_2r": qty_2,
                    "amount_usd": round(amount, 2),
                    "entry": round(entry, 2),
                    "stop": round(stop, 2),
                    "target_1r": round(t1, 2),
                    "target_2r": round(t2, 2),
                    "capital": round(qty * entry, 2),
                    "is_two_tier": is_two_tier,
                    "order_ids": order_ids,
                    "statuses": [o.status for o in orders],
                })
                logger.info(
                    "[trade] %s qty=%d (split %d+%d, $%.0f) entry=$%.2f stop=$%.2f t1=$%.2f t2=$%.2f orders=%s",
                    sym, qty, qty_1, qty_2, amount, entry, stop, t1, t2,
                    [oid[:12] for oid in order_ids],
                )
            except Exception as exc:
                out["skipped"].append({"symbol": sym, "reason": f"order_fail: {exc}"})
                logger.exception("[trade] place_bracket_order failed for %s", sym)

        out["total_budget_usd"] = round(total_budget, 2)
        out["sector_count"] = dict(sector_count)
        out["status"] = "ok"
    finally:
        await adapter.close()

    return out


async def _ensure_protective_stops(adapter, target: date) -> dict:
    """보유 포지션 중 SELL stop이 없는 종목에 자동으로 stop sell 발송.

    트리거 조건: 보유 qty > 0 + 같은 symbol의 open SELL stop / stop_limit 없음
    Stop 가격 결정:
      1) trade_plans.stop_price (해당 종목, 가장 최근 date) — 우선
      2) avg_entry_price × (1 - FALLBACK_PCT) — plan 없으면 fallback (기본 5%)

    발송: alpaca-py StopOrderRequest, time_in_force=GTC
    AUTO_TRADE_ENABLED=false면 dry-run으로 로그만.
    """
    import os
    from sqlalchemy import select
    from api.db.models import TradePlan
    from api.db.session import async_session_factory

    out: dict = {
        "checked": [],
        "already_protected": [],
        "added": [],
        "skipped": [],
        "errors": [],
    }

    positions = await adapter.get_positions()
    if not positions:
        out["status"] = "no_positions"
        return out

    open_orders = await adapter.get_orders(status="open")
    # 종목별 SELL stop 보유 여부
    has_sell_stop: dict[str, bool] = {}
    for o in open_orders:
        if o.side.lower() == "sell" and "stop" in o.order_type.lower():
            has_sell_stop[o.symbol.upper()] = True

    fallback_pct = float(os.environ.get("PROTECTIVE_STOP_FALLBACK_PCT", "5.0"))
    auto_trade = os.environ.get("AUTO_TRADE_ENABLED", "false").strip().lower() == "true"

    # plan 조회 (오늘 + 최근 30일 — 다음날 보유 가능성)
    async with async_session_factory() as session:
        cutoff = target - timedelta(days=30)
        plan_stmt = (
            select(TradePlan)
            .where(TradePlan.plan_date >= cutoff)
            .where(TradePlan.plan_date <= target)
            .order_by(TradePlan.plan_date.desc(), TradePlan.id.desc())
        )
        all_plans = list((await session.execute(plan_stmt)).scalars().all())
    plan_by_symbol: dict[str, TradePlan] = {}
    for p in all_plans:
        key = p.symbol.upper()
        if key not in plan_by_symbol:  # 최근 plan 우선
            plan_by_symbol[key] = p

    for pos in positions:
        sym = pos.symbol.upper()
        out["checked"].append(sym)
        if has_sell_stop.get(sym):
            out["already_protected"].append(sym)
            continue
        if pos.qty <= 0:
            out["skipped"].append({"symbol": sym, "reason": "qty_zero"})
            continue

        plan = plan_by_symbol.get(sym)
        if plan and plan.stop_price:
            stop_price = float(plan.stop_price)
            source = f"plan(id={plan.id}, date={plan.plan_date})"
        else:
            # fallback: avg_entry × (1 - fallback_pct/100)
            stop_price = round(pos.avg_entry_price * (1 - fallback_pct / 100), 2)
            source = f"fallback(avg_entry × {1 - fallback_pct/100:.3f})"

        if stop_price >= pos.current_price:
            # 현재가가 이미 stop 아래로 떨어진 경우 — stop 발송하면 즉시 trigger
            # 보수적으로 skip + alert
            out["skipped"].append({
                "symbol": sym,
                "reason": f"stop_price ${stop_price:.2f} >= current ${pos.current_price:.2f} — 즉시 trigger 위험",
                "stop_price": stop_price,
                "current_price": pos.current_price,
                "source": source,
            })
            continue

        if not auto_trade:
            logger.warning(
                "[monitor.protect] [DRY RUN] %s qty=%d stop=$%.2f (%s) — AUTO_TRADE_ENABLED=false",
                sym, pos.qty, stop_price, source,
            )
            out["added"].append({
                "symbol": sym, "qty": pos.qty, "stop_price": stop_price,
                "source": source, "dry_run": True,
            })
            continue

        # 실 발송
        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            req = StopOrderRequest(
                symbol=sym, qty=pos.qty, side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC, stop_price=stop_price,
            )
            submitted = await asyncio.to_thread(adapter._client.submit_order, order_data=req)
            order_id = str(submitted.id)
            logger.info(
                "[monitor.protect] %s qty=%d stop=$%.2f (%s) order_id=%s",
                sym, pos.qty, stop_price, source, order_id[:12],
            )
            out["added"].append({
                "symbol": sym, "qty": pos.qty, "stop_price": stop_price,
                "source": source, "order_id": order_id, "avg_entry": pos.avg_entry_price,
                "current_price": pos.current_price,
            })
        except Exception as exc:
            logger.exception("[monitor.protect] %s submit failed", sym)
            out["errors"].append({"symbol": sym, "error": f"{exc.__class__.__name__}: {exc}"})

    out["status"] = "ok"
    return out


async def dispatch_plan_immediately(plan_id: int) -> dict:
    """단일 trade_plan을 즉시 발송 (advisor approve, 수동 트리거 등 외부 호출).

    run_trade의 plan-별 안전장치 + bracket 발송과 동일 로직 1회 실행. 멱등:
    plan.broker_order_ids가 이미 있으면 'already_sent' 반환.

    안전장치 (run_trade와 동등):
      - AUTO_TRADE_ENABLED → adapter 측 dry-run
      - regime defensive 차단
      - account.trading_blocked
      - daily loss halt/close (-3% / -5%)
      - position cap 5종목
      - 동일 종목 보유/pending 중복
      - 가격 검증 (entry/stop/t1/t2 정합)
      - buying power
    (sector cap은 broker positions에 sector 정보가 없어 immediate dispatch에선 생략;
     cron run_trade는 trade_plans 기반으로 검증 — 정확한 sector cap은 cron 경로 우위)
    """
    import os
    from collections import Counter
    from sqlalchemy import select
    from api.db.models import TradePlan
    from api.db.session import async_session_factory
    from broker_adapter import get_adapter
    from broker_adapter.alpaca_adapter import _penny
    from broker_adapter.base import BracketOrderRequest, qty_split_50_50
    from scanner.regime import evaluate_regime

    out: dict = {
        "plan_id": plan_id,
        "auto_trade_enabled": os.environ.get("AUTO_TRADE_ENABLED", "false").lower() == "true",
    }

    async with async_session_factory() as session:
        plan = await session.get(TradePlan, plan_id)
    if plan is None:
        return {**out, "status": "error", "reason": f"plan {plan_id} not found"}

    target = plan.plan_date
    sym = plan.symbol.upper()
    out["symbol"] = sym
    out["date"] = target.isoformat()

    if plan.broker_order_ids:
        return {**out, "status": "already_sent", "broker_order_ids": plan.broker_order_ids}

    regime = evaluate_regime(target)
    out["regime_mode"] = regime.mode
    if regime.long_blocked():
        return {**out, "status": "blocked", "reason": f"regime defensive ({regime.mode})"}

    adapter = get_adapter()
    try:
        acc = await adapter.get_account()
        if acc.trading_blocked:
            return {**out, "status": "blocked", "reason": "account trading blocked"}

        halt_pct = float(os.environ.get("DAILY_LOSS_HALT_PCT", "-3.0"))
        close_pct = float(os.environ.get("DAILY_LOSS_CLOSE_PCT", "-5.0"))
        auto_close = os.environ.get("DAILY_LOSS_AUTO_CLOSE", "false").lower() == "true"
        loss = await _check_daily_loss_limit(
            adapter, acc, halt_pct=halt_pct, close_pct=close_pct, auto_close=auto_close,
        )
        if loss["status"] in ("close_all", "halt_new"):
            return {**out, "status": "blocked",
                    "reason": f"daily_loss_{loss['status']} (PnL {loss['daily_pnl_pct']:.2f}%)"}

        positions = await adapter.get_positions()
        held = {p.symbol for p in positions}
        open_orders = await adapter.get_orders(status="open")
        pending_count = Counter(o.symbol for o in open_orders)

        if sym in held:
            return {**out, "status": "skipped", "reason": "already_held"}
        if pending_count.get(sym, 0) >= 2:
            return {**out, "status": "skipped",
                    "reason": f"pending_{pending_count[sym]}_orders"}

        position_cap = int(os.environ.get("POSITION_CAP", "5"))
        if len(positions) >= position_cap:
            return {**out, "status": "blocked",
                    "reason": f"position_cap ({len(positions)}/{position_cap})"}

        entry = float(plan.entry_price)
        stop = float(plan.stop_price)
        t1 = float(plan.target_1r)
        t2 = float(plan.target_2r)
        qty = int(plan.shares)

        if entry <= 0 or stop <= 0 or t1 <= 0 or t2 <= 0 or stop >= entry or t1 <= entry:
            return {**out, "status": "skipped", "reason": "invalid_prices"}
        if t2 <= t1 or _penny(t1) == _penny(t2):
            return {**out, "status": "skipped", "reason": "invalid_targets"}
        if qty <= 0:
            return {**out, "status": "skipped", "reason": "qty_zero"}

        need = qty * entry
        if need > acc.buying_power:
            return {**out, "status": "skipped",
                    "reason": f"insufficient_buying_power need=${need:,.0f} bp=${acc.buying_power:,.0f}"}

        qty_1, qty_2 = qty_split_50_50(qty)
        is_two_tier = qty_2 > 0
        if is_two_tier:
            req = BracketOrderRequest(
                symbol=sym, qty=qty, side="BUY",
                entry_type="stop_limit",
                entry_price=entry, stop_loss_price=stop,
                time_in_force="gtc", is_two_tier=True,
                target_1_price=t1, target_1_qty=qty_1,
                target_2_price=t2, target_2_qty=qty_2,
            )
        else:
            req = BracketOrderRequest(
                symbol=sym, qty=qty, side="BUY",
                entry_type="stop_limit",
                entry_price=entry, stop_loss_price=stop,
                take_profit_price=t1, time_in_force="gtc",
            )

        try:
            orders = await adapter.place_bracket_order(req)
        except Exception as exc:
            logger.exception("[dispatch_immediately] place_bracket_order failed for %s", sym)
            return {**out, "status": "error",
                    "reason": f"order_fail: {exc.__class__.__name__}: {exc}"}

        order_ids = [o.order_id for o in orders]
        async with async_session_factory() as s:
            p = await s.get(TradePlan, plan_id)
            if p:
                p.broker_order_ids = order_ids
                p.confirm_status = "sent"
                await s.commit()

        logger.info(
            "[dispatch_immediately] %s qty=%d entry=$%.2f stop=$%.2f t1=$%.2f t2=$%.2f orders=%s",
            sym, qty, entry, stop, t1, t2, [oid[:12] for oid in order_ids],
        )
        return {**out, "status": "ok", "qty": qty, "is_two_tier": is_two_tier,
                "order_ids": order_ids}
    finally:
        await adapter.close()


async def run_intraday_loop(target: date, *, force_check: bool = False) -> dict:
    """Phase: intraday AI advisor monitor.

    cron이 매 15분 호출 (09:50~15:55 ET, 트리거 기반).
    또는 force_check=True로 매 시간 정기 검토 cron (09:30/10:30/.../15:30, 7회/일).
    ADVISOR_ENABLED=true일 때만 실효.

    monitor phase와 분리: monitor는 stop breakeven 갱신, intraday-loop는 AI 자문.
    """
    from api.db import async_session_factory
    from services.advisor.intraday_monitor import run_intraday_loop_iteration

    async with async_session_factory() as session:
        result = await run_intraday_loop_iteration(session, target, force_check=force_check)
    logger.info(
        "[intraday-loop] force=%s status=%s monitor=%d triggered=%d errors=%d",
        force_check,
        result.get("status"),
        len(result.get("monitor_set", [])),
        len(result.get("triggered", [])),
        len(result.get("errors", [])),
    )
    return result


async def run_eod_close(target: date) -> dict:
    """Phase 7: EOD square-off — 15:55 ET 호출.

    Day trading 정책 — 보유 포지션을 시장가로 청산하고 미체결 bracket parent를 cancel.
    overnight gap 위험 회피.

    안전장치:
      - EOD_AUTO_CLOSE_ENABLED 환경변수 default false → 사용자가 명시 활성화해야 동작
      - AUTO_TRADE_ENABLED=false → adapter가 dry-run으로 처리 (close_position/cancel_order 모두 로그만)
      - 청산된 plan의 score_meta['eod_squared_off']=date 마킹 (멱등 추적)

    환경변수:
      EOD_AUTO_CLOSE_ENABLED=true   — 이 phase 실제 동작
      EOD_CLOSE_PENDING_ORDERS=true — 미체결 bracket parent도 cancel (default true)
    """
    import os

    from sqlalchemy import select

    from api.db.models import TradePlan
    from api.db.session import async_session_factory
    from broker_adapter import get_adapter

    out: dict = {
        "date": target.isoformat(),
        "auto_trade_enabled": os.environ.get("AUTO_TRADE_ENABLED", "false").lower() == "true",
        "closed_positions": [],
        "cancelled_orders": [],
        "skipped": [],
    }

    eod_enabled = (
        os.environ.get("EOD_AUTO_CLOSE_ENABLED", "false").strip().lower() == "true"
    )
    if not eod_enabled:
        out["status"] = "disabled"
        out["reason"] = (
            "EOD_AUTO_CLOSE_ENABLED=false — EOD 자동 청산 비활성화. "
            "활성화하려면 .env에 EOD_AUTO_CLOSE_ENABLED=true 추가."
        )
        logger.info("[eod_close] SKIPPED — EOD_AUTO_CLOSE_ENABLED=false")
        return out

    cancel_pending = (
        os.environ.get("EOD_CLOSE_PENDING_ORDERS", "true").strip().lower() == "true"
    )

    adapter = get_adapter()
    try:
        # 1) 보유 포지션 → 시장가 청산
        positions = await adapter.get_positions()
        out["positions_count"] = len(positions)
        for p in positions:
            try:
                ok = await adapter.close_position(p.symbol)
                if ok:
                    out["closed_positions"].append({
                        "symbol": p.symbol,
                        "qty": p.qty,
                        "avg_entry": p.avg_entry_price,
                        "current": p.current_price,
                        "unrealized_pl": p.unrealized_pl,
                    })
                    logger.info(
                        "[eod_close] CLOSED %s qty=%d pnl=$%+,.2f (%.2f%%)",
                        p.symbol, p.qty, p.unrealized_pl, p.unrealized_pl_pct,
                    )
                else:
                    out["skipped"].append({"symbol": p.symbol, "reason": "close_position_returned_false"})
            except Exception as exc:
                out["skipped"].append({"symbol": p.symbol, "reason": f"close_fail: {exc}"})
                logger.exception("[eod_close] close_position failed for %s", p.symbol)

        # 2) 미체결 bracket parent cancel (overnight 잔여 주문 정리)
        if cancel_pending:
            open_orders = await adapter.get_orders(status="open", nested=True)
            out["pending_count"] = len(open_orders)
            for o in open_orders:
                try:
                    ok = await adapter.cancel_order(o.order_id)
                    if ok:
                        out["cancelled_orders"].append({
                            "symbol": o.symbol,
                            "order_id": o.order_id,
                            "side": o.side,
                            "type": o.order_type,
                        })
                        logger.info(
                            "[eod_close] CANCELLED %s %s %s order_id=%s",
                            o.symbol, o.side, o.order_type, o.order_id[:12],
                        )
                except Exception as exc:
                    out["skipped"].append({
                        "symbol": o.symbol,
                        "reason": f"cancel_fail order_id={o.order_id[:12]}: {exc}",
                    })
                    logger.exception("[eod_close] cancel_order failed for %s", o.order_id)

        # 3) 오늘 발송된 plan에 eod_squared_off 마킹 (멱등 추적)
        async with async_session_factory() as session:
            stmt = (
                select(TradePlan)
                .where(TradePlan.plan_date == target)
                .where(TradePlan.broker_order_ids.is_not(None))
            )
            plans = list((await session.execute(stmt)).scalars().all())
            marked = 0
            for plan in plans:
                p = await session.get(TradePlan, plan.id)
                if p:
                    meta = dict(p.score_meta or {})
                    meta["eod_squared_off"] = target.isoformat()
                    p.score_meta = meta
                    marked += 1
            await session.commit()
            out["plans_marked"] = marked
    finally:
        await adapter.close()

    out["status"] = "ok"
    return out


async def run_all(target: date, lookback: int = 30) -> dict:
    """log + backfill 순차 실행. 한쪽 실패해도 다른쪽 시도 (멱등성)."""
    started = datetime.now()
    out: dict = {
        "date": target.isoformat(),
        "started_at": started.isoformat(),
    }
    try:
        out["log"] = await run_log(target)
    except Exception as exc:
        logger.exception("log_daily_picks failed")
        out["log_error"] = str(exc)
    try:
        out["backfill"] = await run_backfill(target, lookback)
    except Exception as exc:
        logger.exception("backfill_pick_outcomes failed")
        out["backfill_error"] = str(exc)
    finished = datetime.now()
    out["finished_at"] = finished.isoformat()
    out["duration_sec"] = round((finished - started).total_seconds(), 2)
    return out


async def _summarize_unsent_plans(target: date) -> dict:
    """5.2 Crash log helper — target 날짜의 user_fixed 미발송 plan 요약.

    crash 알림에 포함되어 사용자가 다음 cron 발송 전 수동 개입 여부 판단.
    """
    from sqlalchemy import select

    from api.db import async_session_factory
    from api.db.models import TradePlan

    async with async_session_factory() as s:
        stmt = (
            select(TradePlan)
            .where(TradePlan.plan_date == target)
            .where(TradePlan.dispatch_mode == "user_fixed")
        )
        rows = (await s.execute(stmt)).scalars().all()
    unsent = [
        {"symbol": r.symbol, "rank": r.rank}
        for r in rows
        if not r.broker_order_ids
    ]
    sent = [r.symbol for r in rows if r.broker_order_ids]
    return {
        "user_fixed_total": len(rows),
        "sent_count": len(sent),
        "unsent_count": len(unsent),
        "unsent": unsent,
        "sent_symbols": sent,
    }


async def _with_advisory_lock(phase: str, factory):
    """PostgreSQL pg_try_advisory_lock으로 phase 동시 실행 차단.

    같은 phase 이름 cron + 수동 실행이 충돌해도 두 번째는 즉시 status=locked로 종료.
    lock은 connection scope — 종료 시 자동 release + 명시적 unlock.
    """
    import hashlib

    from sqlalchemy import text

    from api.db import async_session_factory

    digest = hashlib.sha256(f"daily_pipeline:{phase}".encode()).digest()
    # signed bigint range: PostgreSQL pg_advisory_lock(bigint) 호환.
    key = int.from_bytes(digest[:8], "big", signed=True)

    session = async_session_factory()
    try:
        acquired = (
            await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        ).scalar()
        if not acquired:
            logger.warning(
                "[lock] phase=%s already running (key=%d) — skipping this invocation",
                phase, key,
            )
            return {
                "phase": phase,
                "status": "locked",
                "reason": (
                    f"another daily_pipeline phase={phase!r} is currently running. "
                    "이 호출은 즉시 종료. 중복 발송 방지."
                ),
            }
        try:
            return await factory()
        finally:
            await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=[
            "log", "preopen", "confirm", "backfill", "trade", "monitor",
            "intraday-loop", "all",
        ],
        default="all",
        help=(
            "log: 09:00 picks 적재 / preopen: 09:25 watchlist (orb_auto 마킹) + AI morning brief / "
            "trade: 09:30 dispatch_mode=user_fixed 입력값 그대로 발송 / "
            "confirm: 09:45 dispatch_mode=orb_auto에 ORB+VWAP+RVOL 평가 후 발송 / "
            "backfill: outcome 채움 / "
            "monitor: 1차 hit 후 stop breakeven 갱신 / "
            "intraday-loop: AI 자문 15분 트리거 (09:50~15:55) / all: log+backfill"
        ),
    )
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="대상 날짜 (기본 오늘)",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=30,
        help="backfill 시 거슬러 올라갈 일수 (기본 30)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="intraday-loop: 트리거/dedupe 무시하고 monitor set 전종목 LLM 호출 (정기 검토 cron 용)",
    )
    args = parser.parse_args()

    target = args.date or date.today()
    logger.info(
        "=== daily_pipeline phase=%s target=%s ===", args.phase, target
    )

    # Weekend 안전망 — log/preopen/confirm/trade/monitor/intraday-loop는 거래일 외 무용. backfill은 OK.
    is_weekend = target.weekday() >= 5  # 5=Sat, 6=Sun
    if is_weekend and args.phase in (
        "log", "preopen", "confirm", "trade", "monitor", "intraday-loop",
    ):
        result = {
            "date": target.isoformat(),
            "phase": args.phase,
            "status": "weekend_skip",
            "reason": f"market closed (weekday={target.weekday()})",
        }
        print(json.dumps(result, indent=2, default=str))
        logger.info("[weekend skip] phase=%s target=%s", args.phase, target)
        return

    from notifications import send_failure_alert, send_heartbeat

    send_heartbeat(args.phase, "started", details={"target": target.isoformat()})

    try:
        if args.phase == "log":
            result = asyncio.run(_with_advisory_lock("log", lambda: run_log(target)))
        elif args.phase == "preopen":
            result = asyncio.run(_with_advisory_lock("preopen", lambda: run_preopen(target)))
        elif args.phase == "confirm":
            result = asyncio.run(_with_advisory_lock("confirm", lambda: run_confirm_phase(target)))
        elif args.phase == "backfill":
            result = asyncio.run(_with_advisory_lock("backfill", lambda: run_backfill(target, args.lookback)))
        elif args.phase == "trade":
            result = asyncio.run(_with_advisory_lock("trade", lambda: run_trade(target)))
        elif args.phase == "monitor":
            result = asyncio.run(_with_advisory_lock("monitor", lambda: run_monitor(target)))
        elif args.phase == "intraday-loop":
            result = asyncio.run(_with_advisory_lock(
                "intraday-loop",
                lambda: run_intraday_loop(target, force_check=args.force),
            ))
        else:
            result = asyncio.run(_with_advisory_lock("all", lambda: run_all(target, args.lookback)))
    except Exception as exc:
        # 5.2 Crash log — crash 시점의 미발송 user_fixed plan을 자동 감지해 alert에 포함.
        # 멱등성으로 다음 cron이 자동 resume하지만, 사용자가 즉시 인지할 수 있게.
        try:
            unsent_info = asyncio.run(_summarize_unsent_plans(target))
        except Exception as _scan_exc:
            unsent_info = {"scan_error": str(_scan_exc)}
        logger.error(
            "[crash] phase=%s target=%s — 미발송 plan: %s",
            args.phase, target, unsent_info,
        )
        send_failure_alert(
            args.phase,
            exc,
            message=f"daily_pipeline.{args.phase} crashed",
            details={"target": target.isoformat(), "unsent_plans": unsent_info},
        )
        raise

    print(json.dumps(result, indent=2, default=str))
    logger.info("=== pipeline completed ===")
    send_heartbeat(
        args.phase, "completed",
        details={"summary": _summarize_result(args.phase, result)},
    )


def _summarize_result(phase: str, result: dict) -> dict:
    """Heartbeat 이메일 본문에 들어갈 요약."""
    if phase == "log":
        return {"picks_logged": result}
    if phase == "preopen":
        return {
            "status": result.get("status"),
            "watchlist_count": result.get("pick_count"),
            "symbols": [w.get("symbol") for w in result.get("watchlist", [])],
            "skipped": len(result.get("skipped", [])),
        }
    if phase == "confirm":
        return {
            "status": result.get("status"),
            "watchlist_count": result.get("watchlist_count"),
            "passed_count": result.get("passed_count", 0),
            "orders_sent": len(result.get("orders", [])),
            "failed_count": result.get("failed_count", 0),
            "skipped": len(result.get("skipped", [])),
        }
    if phase == "trade":
        return {
            "status": result.get("status"),
            "plans_count": result.get("plans_count"),
            "orders_sent": len(result.get("orders", [])),
            "skipped": len(result.get("skipped", [])),
            "daily_pnl_pct": result.get("daily_loss_check", {}).get("daily_pnl_pct"),
        }
    if phase == "backfill":
        recon = result.get("reconciliation")
        return {
            "pick_outcomes": result.get("pick_outcomes"),
            "trade_plan_outcomes": result.get("trade_plan_outcomes"),
            "discrepancies": len(recon.get("discrepancies", [])) if recon else None,
        }
    if phase == "monitor":
        return {
            "status": result.get("status"),
            "plans_count": result.get("plans_count"),
            "raised": len(result.get("raised", [])),
            "skipped": len(result.get("skipped", [])),
        }
    if phase == "intraday-loop":
        return {
            "status": result.get("status"),
            "monitor_set": len(result.get("monitor_set", [])),
            "triggered": len(result.get("triggered", [])),
            "skipped_dedupe": len(result.get("skipped_dedupe", [])),
            "errors": len(result.get("errors", [])),
        }
    return {}


if __name__ == "__main__":
    main()
