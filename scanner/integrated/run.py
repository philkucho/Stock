"""Integrated picks 산출기.

오늘 picks: 사용자 호출 또는 cron이 매일 09:25 ET에 호출 (logger 통해).
Historical: scanner_picks + 일봉 기반 v3 quality layer로 backfill 가능.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from backtests.data_cache import get_bars
from scanner.comparison.adapters import PickCandidate, fetch_scanner_picks
from scanner.regime import evaluate_regime
from signals.compression_expansion import detect_compression_expansion
from signals.open_location import compute_open_location
from signals.rsi_structure import detect_rsi_structure

logger = logging.getLogger(__name__)


# Composite weighting v1 (scanner 중심)
W_SCANNER = 50.0
W_COMPRESSION = 25.0
W_OPEN_LOC = 15.0
W_RSI = 10.0

# Composite weighting v2 (v3 quality 강화 + sector concentration)
W_V2_SCANNER = 30.0       # scanner 5점 만점 → 30점
W_V2_COMPRESSION = 35.0   # C5 6점 만점 → 35점 (v3 핵심)
W_V2_OPEN_LOC = 20.0      # C4 5점 만점 → 20점
W_V2_RSI = 15.0           # D1 5점 만점 → 15점
W_V2_SECTOR_BONUS = 10.0  # 반도체장비/모멘텀 sector concentration

PRIORITY_SECTORS = {
    "Semiconductor Equipment & Materials",
    "Semiconductors",
    "Information Technology",
    "Technology",
}


def _slice_to_date(bars: pd.DataFrame, target_date: date) -> pd.DataFrame:
    if bars is None or bars.empty:
        return pd.DataFrame()
    target_ts = pd.Timestamp(target_date, tz="UTC")
    return bars[bars.index <= target_ts]


async def run_integrated_v2(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v2 — Universe 통합(v3 ∪ scanner) + v3 quality 가중치 ↑ + sector concentration.

    v1 분석 결과: scanner의 broad pool로 v3 layer를 적용해도 v3 단독 5d/10d 알파 못 넘음.
    v2 가설: v3 universe(반도체/모멘텀) + scanner WHITELIST 합집합에 v3 quality + sector bonus
            → v3의 강한 setup pool을 보존하면서 scanner의 신호도 활용.
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory

    # 1) Regime gate
    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v2: defensive regime — long blocked")
        return []

    # 2) Universe 통합 — scanner + v3 candidates union
    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)

    # v3 candidates (historical 또는 today)
    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    # Symbol union (중복 제거, score는 양쪽 보존)
    pool: dict[str, dict] = {}
    for sp in scanner_picks:
        pool[sp.symbol] = {
            "symbol": sp.symbol,
            "scanner_score": sp.score,  # 0-5
            "scanner_meta": sp.score_meta,
            "v3_score": 0.0,
            "v3_meta": {},
            "sector": sp.sector,
        }
    for vp in v3_picks:
        if vp.symbol in pool:
            pool[vp.symbol]["v3_score"] = vp.score  # 0-100
            pool[vp.symbol]["v3_meta"] = vp.score_meta
            if pool[vp.symbol]["sector"] is None:
                pool[vp.symbol]["sector"] = vp.sector
        else:
            pool[vp.symbol] = {
                "symbol": vp.symbol,
                "scanner_score": 0.0,
                "scanner_meta": {},
                "v3_score": vp.score,
                "v3_meta": vp.score_meta,
                "sector": vp.sector,
            }

    if not pool:
        return []

    # 3) v3 quality layer + composite scoring
    enriched: list[tuple[float, PickCandidate, dict]] = []
    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=120)).isoformat()

    for sym, data in pool.items():
        try:
            full_bars = get_bars(sym, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50:
                continue

            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad":
                continue

            last = bars.iloc[-1]
            prev = bars.iloc[-2] if len(bars) >= 2 else last
            open_loc = compute_open_location(
                open_price=float(last["open"]),
                pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]),
                prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk:
                continue

            # v2 composite (총 110점, sector bonus 별도)
            scanner_norm = (data["scanner_score"] / 5.0) * W_V2_SCANNER
            ce_norm = (ce.score / 6.0) * W_V2_COMPRESSION
            ol_norm = (open_loc.score / 5.0) * W_V2_OPEN_LOC
            rsi_norm = (rsi.score / 5.0) * W_V2_RSI

            # Sector bonus
            sector = data["sector"]
            sector_bonus = 0.0
            if sector and any(p in sector for p in PRIORITY_SECTORS):
                sector_bonus = W_V2_SECTOR_BONUS

            # v3 score 보너스 (v3 60점 통과 시 +5)
            v3_passed_bonus = 5.0 if data["v3_score"] >= 60 else 0.0

            composite = (
                scanner_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + v3_passed_bonus
            )

            quality_meta = {
                "scanner_score": data["scanner_score"],
                "v3_score": data["v3_score"],
                "v3_passed": data["v3_score"] >= 60,
                "compression": ce.is_compression,
                "expansion": ce.is_expansion,
                "compression_score": ce.score,
                "open_location_score": open_loc.score,
                "rsi_grade": rsi.grade,
                "rsi_value": round(rsi.rsi_value, 2),
                "sector_bonus": sector_bonus,
                "v3_passed_bonus": v3_passed_bonus,
                "regime_score": regime.score,
                "version": "v2",
            }
            enriched.append((composite, sym, sector, quality_meta))
        except Exception as exc:
            logger.warning("Integrated v2 layer failed for %s: %s", sym, exc)

    # 4) Composite 정렬, top N
    enriched.sort(key=lambda x: x[0], reverse=True)
    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(enriched[:top], start=1):
        out.append(
            PickCandidate(
                system_id="integrated",
                rank=i,
                symbol=sym,
                score=round(composite, 2),
                score_meta=meta,
                sector=sector,
                strategy_tag="swing",
            )
        )
    return out


async def run_integrated_v10(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v10 — Confluence super-multiplier + Auto-blacklist + Drawdown-aware.

    v9 한계: 5d/10d 알파 plateau (7.04%/12.28%). 추가 시그널 ROI 감소.
    v10 가설:
      1. Confluence super-multiplier ×1.3 — 5개 강한 신호 동시 충족 시
         (v3_passed + scanner≥4 + compression+expansion + streak≥3 + rsi_good)
         → 정말 강한 setup만 boost
      2. Auto-blacklist — 30일 내 feedback reject 2회+ symbol 영구 제외
         → 시스템 self-blacklist
      3. Drawdown-aware mode — recent 5 outcomes 중 -5%↓ 2개+ 발생 시 defensive
         → compression OR rsi_good OR avwap_above 필수
      4. v9 모든 기능 유지
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory
    from api.db.models import SystemPickLog, PickOutcome
    from scanner.benchmarks import sector_etf_for, get_benchmark_bars
    from scanner.catalysts import nasdaq_earnings
    from signals.relative_strength import rs_vs_benchmark
    from signals.stage2_trend_template import trend_template_pass
    from sqlalchemy import select

    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v10: defensive regime — long blocked")
        return []
    regime_boost = 1.2 if regime.mode == "aggressive" else 1.0

    vix_high = False
    try:
        vix_value = float(regime.diagnostics.get("vix_value", 0)) if regime.diagnostics.get("vix_value") else None
        if vix_value and vix_value > 25:
            vix_high = True
    except Exception:
        pass

    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)
    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    scanner_by_sym = {sp.symbol: sp for sp in scanner_picks}
    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=400)).isoformat()
    today_d = target_date or date.today()
    streak_lookback_start = today_d - timedelta(days=10)
    feedback_lookback_start = today_d - timedelta(days=30)
    blacklist_lookback_start = today_d - timedelta(days=30)

    v3_streak: dict[str, int] = {}
    feedback_data: dict[str, list[tuple[date, float]]] = {}
    auto_blacklist: set[str] = set()
    drawdown_mode = False
    sess = session
    sess_owns = sess is None
    if sess_owns:
        sess = async_session_factory()
    try:
        # Streak
        stmt = select(SystemPickLog.symbol).where(
            SystemPickLog.system_id == "v3",
            SystemPickLog.pick_date >= streak_lookback_start,
            SystemPickLog.pick_date < today_d,
        )
        for row in (await sess.execute(stmt)).all():
            v3_streak[row[0]] = v3_streak.get(row[0], 0) + 1

        # Feedback alpha (5d horizon)
        stmt2 = (
            select(SystemPickLog.symbol, SystemPickLog.pick_date, PickOutcome.alpha)
            .join(PickOutcome, PickOutcome.pick_log_id == SystemPickLog.id)
            .where(
                SystemPickLog.pick_date >= feedback_lookback_start,
                SystemPickLog.pick_date < today_d,
                PickOutcome.horizon_days == 5,
            )
        )
        all_feedback_rows = (await sess.execute(stmt2)).all()
        for row in all_feedback_rows:
            feedback_data.setdefault(row[0], []).append((row[1], float(row[2])))

        # v10: Auto-blacklist — 30일 내 reject 2회+ symbol
        reject_count: dict[str, int] = {}
        for sym, alphas_by_date in feedback_data.items():
            # 동일 symbol의 reject 누적: alpha < -1.0인 outcome 카운트
            cnt = sum(1 for _, a in alphas_by_date if a < -1.0)
            if cnt >= 2:
                auto_blacklist.add(sym)
                reject_count[sym] = cnt

        # v10: Drawdown-aware — integrated 시스템의 recent 5 picks의 outcome 확인
        stmt3 = (
            select(SystemPickLog.pick_date, PickOutcome.alpha)
            .join(PickOutcome, PickOutcome.pick_log_id == SystemPickLog.id)
            .where(
                SystemPickLog.system_id == "integrated",
                SystemPickLog.pick_date >= today_d - timedelta(days=10),
                SystemPickLog.pick_date < today_d,
                PickOutcome.horizon_days == 5,
            )
        )
        recent_drawdowns = sum(1 for row in (await sess.execute(stmt3)).all() if float(row[1]) < -5.0)
        if recent_drawdowns >= 2:
            drawdown_mode = True
            logger.warning("v10 drawdown mode ON — recent 5d drawdowns: %d", recent_drawdowns)

    except Exception as exc:
        logger.warning("v10 query failed: %s", exc)
    finally:
        if sess_owns:
            await sess.close()

    if auto_blacklist:
        logger.info("v10 auto-blacklist (%d): %s", len(auto_blacklist), list(auto_blacklist)[:10])

    spy_full = get_benchmark_bars("SPY", lookback_days=400)
    spy_bars_full = _slice_to_date(spy_full, target_date) if (spy_full is not None and target_date) else spy_full
    sector_momentum_cache: dict[str, float] = {}

    def _sector_momentum(sector: str | None) -> float:
        if not sector: return 0.0
        etf = sector_etf_for(sector)
        if not etf: return 0.0
        if etf in sector_momentum_cache: return sector_momentum_cache[etf]
        try:
            etf_full = get_benchmark_bars(etf, lookback_days=30)
            etf_bars = _slice_to_date(etf_full, target_date) if target_date else etf_full
            if etf_bars is None or len(etf_bars) < 6 or spy_bars_full is None or len(spy_bars_full) < 6:
                sector_momentum_cache[etf] = 0.0; return 0.0
            etf_5d = float(etf_bars["close"].iloc[-1] / etf_bars["close"].iloc[-6]) - 1
            spy_5d = float(spy_bars_full["close"].iloc[-1] / spy_bars_full["close"].iloc[-6]) - 1
            sm = etf_5d - spy_5d
            sector_momentum_cache[etf] = sm
            return sm
        except Exception:
            sector_momentum_cache[etf] = 0.0; return 0.0

    def _obv_trend(bars: pd.DataFrame) -> bool:
        if bars is None or len(bars) < 11: return False
        try:
            recent = bars.iloc[-11:]
            close_diff = recent["close"].diff()
            volume = recent["volume"]
            obv = (volume * close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
            return float(obv.iloc[-1]) > float(obv.iloc[0])
        except Exception:
            return False

    def _anchored_vwap_above(bars: pd.DataFrame) -> bool:
        if bars is None or len(bars) < 6: return False
        try:
            recent = bars.iloc[-5:]
            tp = (recent["high"] + recent["low"] + recent["close"]) / 3
            vwap = (tp * recent["volume"]).sum() / recent["volume"].sum()
            return float(bars["close"].iloc[-1]) > float(vwap)
        except Exception:
            return False

    def _has_forward_er(symbol: str) -> bool:
        try:
            er_date = nasdaq_earnings._next_earnings_date(symbol)
            if er_date is None: return False
            delta = (er_date - today_d).days
            return 0 <= delta <= 7
        except Exception:
            return False

    def _momentum_acceleration(bars: pd.DataFrame) -> bool:
        if bars is None or len(bars) < 64 or spy_bars_full is None or len(spy_bars_full) < 64:
            return False
        try:
            rs_1m = rs_vs_benchmark(bars, spy_bars_full, 21)
            rs_3m = rs_vs_benchmark(bars, spy_bars_full, 63)
            if rs_1m is None or rs_3m is None: return False
            return rs_1m > rs_3m
        except Exception:
            return False

    def _eval(symbol: str):
        try:
            full_bars = get_bars(symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50: return None
            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad": return None
            last = bars.iloc[-1]; prev = bars.iloc[-2]
            open_loc = compute_open_location(
                open_price=float(last["open"]), pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]), prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk: return None
            try:
                stage2_ok = bool(trend_template_pass(bars).iloc[-1]) if len(bars) >= 252 else False
            except Exception:
                stage2_ok = False
            obv_up = _obv_trend(bars)
            mom_accel = _momentum_acceleration(bars)
            avwap_above = _anchored_vwap_above(bars)
            return ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel, avwap_above
        except Exception:
            return None

    def _earn_mult(scanner_match) -> float:
        if not scanner_match: return 1.0
        phase = (scanner_match.score_meta or {}).get("earnings_phase")
        return 1.25 if phase == "post" else 1.0

    def _streak_bonus(symbol: str) -> tuple[float, int]:
        cnt = v3_streak.get(symbol, 0)
        if cnt >= 5: return 15.0, cnt
        if cnt >= 3: return 10.0, cnt
        return 0.0, cnt

    def _feedback_decay_cubic(symbol: str) -> tuple[float, float, bool]:
        if symbol not in feedback_data:
            return 0.0, 0.0, False
        weighted_sum = 0.0; weight_total = 0.0
        for pdate, alpha_pct in feedback_data[symbol]:
            age = (today_d - pdate).days
            if age <= 1: w = 4.0
            elif age <= 5: w = 2.0
            elif age <= 15: w = 1.0
            else: w = 0.3
            weighted_sum += alpha_pct * w
            weight_total += w
        if weight_total == 0: return 0.0, 0.0, False
        weighted_avg = weighted_sum / weight_total
        bonus = max(-12.0, min(12.0, weighted_avg * 5.0))
        should_reject = weighted_avg < -1.0
        return bonus, weighted_avg, should_reject

    def _drawdown_mode_check(ce, rsi, avwap_above) -> bool:
        """Drawdown mode일 때 conservative 필터: compression OR rsi_good OR avwap_above 필수."""
        if not drawdown_mode:
            return True
        return ce.is_compression or rsi.grade == "good" or avwap_above

    all_candidates: list[tuple[float, str, str | None, dict]] = []
    seen: set[str] = set()
    rejected_neg: list[str] = []
    rejected_blacklist: list[str] = []

    for vp in v3_picks:
        if vp.symbol in seen: continue
        seen.add(vp.symbol)
        if vp.symbol in auto_blacklist:
            rejected_blacklist.append(vp.symbol); continue
        if _has_forward_er(vp.symbol): continue
        feedback_b, feedback_avg, reject = _feedback_decay_cubic(vp.symbol)
        if reject:
            rejected_neg.append(vp.symbol); continue
        q = _eval(vp.symbol)
        if q is None: continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel, avwap_above = q
        if vix_high and not (ce.is_compression and ce.is_expansion): continue
        if not _drawdown_mode_check(ce, rsi, avwap_above): continue

        v3_norm = ((vp.score / 100.0) ** 1.3) * 50
        ce_norm = (ce.score / 6.0) * 20 * regime_boost
        if ce.is_compression and ce.is_expansion: ce_norm += 10.0
        if stage2_ok and ce.is_compression: ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 8
        rsi_norm = (rsi.score / 5.0) * 5

        sector = vp.sector
        sector_bonus = 10.0 if (sector and any(p in sector for p in PRIORITY_SECTORS)) else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        sm = scanner_by_sym.get(vp.symbol)
        confluence_bonus = 15.0 if (sm and sm.score >= 4) else 0.0
        stage2_bonus = 8.0 if stage2_ok else 0.0
        em = _earn_mult(sm)

        streak_b, streak_count = _streak_bonus(vp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0
        mom_accel_bonus = 5.0 if mom_accel else 0.0
        avwap_bonus = 5.0 if avwap_above else 0.0

        # v10: Confluence super-multiplier ×1.3
        super_confluence = (
            sm is not None and sm.score >= 4 and  # scanner도 통과
            ce.is_compression and ce.is_expansion and  # golden setup
            streak_count >= 3 and  # streak
            rsi.grade == "good"  # RSI 강세
        )
        super_mult = 1.3 if super_confluence else 1.0

        base = (v3_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
                + confluence_bonus + stage2_bonus + streak_b + obv_bonus + mom_accel_bonus
                + avwap_bonus + feedback_b)
        composite = base * em * super_mult

        meta = {
            "tier": 1, "selection_path": "v3_priority",
            "v3_score": vp.score, "v3_passed": True,
            "scanner_score": sm.score if sm else 0.0,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "stage2_pass": stage2_ok,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "confluence_bonus": confluence_bonus,
            "stage2_bonus": stage2_bonus, "earnings_multiplier": em,
            "streak_count": streak_count, "streak_bonus": streak_b,
            "obv_up": obv_up, "obv_bonus": obv_bonus,
            "momentum_accel": mom_accel, "mom_accel_bonus": mom_accel_bonus,
            "avwap_above": avwap_above, "avwap_bonus": avwap_bonus,
            "feedback_avg_alpha": round(feedback_avg, 4),
            "feedback_bonus": round(feedback_b, 2),
            "super_confluence": super_confluence, "super_multiplier": super_mult,
            "drawdown_mode": drawdown_mode,
            "vix_high": vix_high,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v10",
        }
        all_candidates.append((composite, vp.symbol, sector, meta))

    for sp in scanner_picks:
        if sp.symbol in seen: continue
        seen.add(sp.symbol)
        if sp.symbol in auto_blacklist:
            rejected_blacklist.append(sp.symbol); continue
        if _has_forward_er(sp.symbol): continue
        feedback_b, feedback_avg, reject = _feedback_decay_cubic(sp.symbol)
        if reject:
            rejected_neg.append(sp.symbol); continue
        q = _eval(sp.symbol)
        if q is None: continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel, avwap_above = q
        if vix_high and not (ce.is_compression and ce.is_expansion): continue
        if not _drawdown_mode_check(ce, rsi, avwap_above): continue

        cnt = 0
        if ce.is_compression: cnt += 1
        if ce.is_expansion: cnt += 1
        if stage2_ok: cnt += 1
        sector = sp.sector
        sector_priority = bool(sector and any(p in sector for p in PRIORITY_SECTORS))
        if sector_priority: cnt += 1
        if open_loc.above_pivot: cnt += 1
        if rsi.grade == "good": cnt += 1
        if obv_up: cnt += 1
        if mom_accel: cnt += 1
        if avwap_above: cnt += 1

        has_ce = ce.is_compression or ce.is_expansion
        if cnt < 3 or not has_ce: continue

        scanner_norm = (sp.score / 5.0) * 25
        ce_norm = (ce.score / 6.0) * 25 * regime_boost
        if ce.is_compression and ce.is_expansion: ce_norm += 8.0
        if stage2_ok and ce.is_compression: ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 12
        rsi_norm = (rsi.score / 5.0) * 8
        stage2_bonus = 12.0 if stage2_ok else 0.0
        sector_bonus = 10.0 if sector_priority else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        em = _earn_mult(sp)

        streak_b, streak_count = _streak_bonus(sp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0
        mom_accel_bonus = 5.0 if mom_accel else 0.0
        avwap_bonus = 5.0 if avwap_above else 0.0

        # v10 super_confluence (Tier 2도 적용)
        super_confluence = (
            sp.score >= 4 and ce.is_compression and ce.is_expansion
            and streak_count >= 3 and rsi.grade == "good"
        )
        super_mult = 1.3 if super_confluence else 1.0

        base = (scanner_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
                + stage2_bonus + streak_b + obv_bonus + mom_accel_bonus + avwap_bonus + feedback_b)
        composite = base * em * super_mult

        meta = {
            "tier": 2, "selection_path": "scanner_strict_3plus_ce",
            "v3_score": 0, "v3_passed": False,
            "scanner_score": sp.score, "stage2_pass": stage2_ok,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "signals_count": cnt,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "stage2_bonus": stage2_bonus,
            "earnings_multiplier": em, "streak_count": streak_count,
            "streak_bonus": streak_b, "obv_up": obv_up, "obv_bonus": obv_bonus,
            "momentum_accel": mom_accel, "mom_accel_bonus": mom_accel_bonus,
            "avwap_above": avwap_above, "avwap_bonus": avwap_bonus,
            "feedback_avg_alpha": round(feedback_avg, 4),
            "feedback_bonus": round(feedback_b, 2),
            "super_confluence": super_confluence, "super_multiplier": super_mult,
            "drawdown_mode": drawdown_mode,
            "vix_high": vix_high,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v10",
        }
        all_candidates.append((composite, sp.symbol, sector, meta))

    if rejected_neg:
        logger.info("v10 negative reject (%d): %s", len(rejected_neg), rejected_neg[:10])
    if rejected_blacklist:
        logger.info("v10 auto-blacklist reject (%d): %s", len(rejected_blacklist), rejected_blacklist[:10])

    all_candidates.sort(key=lambda x: x[0], reverse=True)

    sector_count: dict[str, int] = {}
    diversified: list[tuple[float, str, str | None, dict]] = []
    for composite, sym, sector, meta in all_candidates:
        cnt_s = sector_count.get(sector or "_unk", 0)
        if cnt_s >= 2:
            penalty = (cnt_s - 1) * 5.0
            adjusted = composite - penalty
            meta["diversification_penalty"] = penalty
        else:
            adjusted = composite
            meta["diversification_penalty"] = 0.0
        sector_count[sector or "_unk"] = cnt_s + 1
        diversified.append((adjusted, sym, sector, meta))

    diversified.sort(key=lambda x: x[0], reverse=True)
    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(diversified[:top], start=1):
        out.append(PickCandidate(
            system_id="integrated", rank=i, symbol=sym,
            score=round(composite, 2), score_meta=meta,
            sector=sector, strategy_tag="swing",
        ))
    return out


# ─────────── Intraday 5-Model Stack (Phase 4: preopen at 09:25 ET) ───────────

# Premarket signal weights (composite re-ranking)
W_INTRA_BASE = 60.0          # v10 score 비례
W_INTRA_GAP = 20.0           # premarket gap (+5% saturate)
W_INTRA_RVOL = 15.0          # premarket RVOL (3x saturate)
W_INTRA_CATALYST = 15.0      # catalyst score / 30 (CATALYST KIND_SCORE max ~30)

INTRA_GAP_HARD_SKIP = 10.0   # gap > +10% → skip (overheated)
INTRA_GAP_LOW_SKIP = -3.0    # gap < -3% → skip (bearish open)
INTRA_SPREAD_MAX = 1.5       # spread > 1.5% → skip (illiquid)

PROVISIONAL_STOP_ATR_MULT = 2.0
PROVISIONAL_TARGET_R_MULT = 1.0


async def run_integrated_intraday(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    candidate_pool: int = 10,
    v10_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """5-Model Intraday Stack — preopen (09:25 ET) watchlist 산출.

    Stack:
      1. integrated v10 (어제 종가 셋업 강도) — candidate_pool top 10
      2. 운영 v3 (real-time fetch_candidate_metrics) — 프리마켓 갭/RVOL/spread
      3. catalyst (aggregator) — 어닝/뉴스 multiplier
      4. regime + sector momentum — 시장 게이트
      5. (Phase 5 confirm — 별도 호출)

    Provisional entry/stop/target_1r/target_2r를 score_meta에 담아 반환.
    실제 ORB 기반 levels는 Phase 5 (intraday_confirm)에서 덮어씀.
    """
    from datetime import timedelta as _td
    from scanner.catalysts.aggregator import aggregate_catalyst
    from scanner.catalysts.types import KIND_SCORE
    from scanner.stage2_daily_picks import fetch_candidate_metrics
    from signals.atr import atr_pct as _atr_pct

    today = target_date or date.today()

    # 1) Regime hard gate
    regime = evaluate_regime(today)
    if regime.long_blocked():
        logger.warning("Intraday stack: defensive regime — long blocked")
        return []

    # 2) v10 candidate pool
    if v10_picks_cached is not None:
        v10_picks = v10_picks_cached
    else:
        v10_picks = await run_integrated_v10(today, candidate_pool, session=session)

    if not v10_picks:
        logger.info("Intraday stack: v10 returned no candidates for %s", today)
        return []

    # 3) Per-candidate premarket fetch + score adjustment
    enriched: list[tuple[float, PickCandidate, dict]] = []
    skipped: list[dict] = []

    end_iso = today.isoformat()
    start_iso = (today - _td(days=60)).isoformat()

    for vp in v10_picks:
        try:
            m = fetch_candidate_metrics(vp.symbol, today)
        except Exception as exc:
            logger.warning("Intraday %s premarket fetch failed: %s", vp.symbol, exc)
            skipped.append({"symbol": vp.symbol, "reason": f"fetch_error: {exc}"})
            continue

        gap_pct = float(m.gap_pct) if m.gap_pct is not None else 0.0
        rvol = float(m.rvol) if m.rvol else 0.0
        spread = float(m.spread_pct) if m.spread_pct is not None else 0.0
        premarket_close = float(m.premarket_close) if m.premarket_close else None
        prev_close = float(m.prev_close) if m.prev_close else None

        # Hard skips
        if gap_pct > INTRA_GAP_HARD_SKIP:
            skipped.append({"symbol": vp.symbol, "reason": f"gap_overheated {gap_pct:.2f}%"})
            continue
        if gap_pct < INTRA_GAP_LOW_SKIP:
            skipped.append({"symbol": vp.symbol, "reason": f"gap_bearish {gap_pct:.2f}%"})
            continue
        if spread and spread > INTRA_SPREAD_MAX:
            skipped.append({"symbol": vp.symbol, "reason": f"spread_too_wide {spread:.2f}%"})
            continue
        if premarket_close is None or prev_close is None or prev_close <= 0:
            skipped.append({"symbol": vp.symbol, "reason": "missing_quote"})
            continue

        # Catalyst
        try:
            catalyst = aggregate_catalyst(vp.symbol, today)
            catalyst_max = max(KIND_SCORE.values()) if KIND_SCORE else 30
            catalyst_norm = (catalyst.score / catalyst_max) if catalyst_max > 0 else 0.0
        except Exception as exc:
            logger.debug("catalyst fetch failed %s: %s", vp.symbol, exc)
            catalyst = None
            catalyst_norm = 0.0

        # Composite scoring — v10 base + premarket signals
        v10_norm = (vp.score / 250.0)  # v10 composite max ~250 → normalize 0~1
        v10_norm = min(1.0, max(0.0, v10_norm))

        gap_norm = max(0.0, min(1.0, gap_pct / 5.0))   # 0~+5% gap saturates at 1.0
        rvol_norm = max(0.0, min(1.0, (rvol - 1.0) / 2.0))  # 1.0~3.0 rvol → 0~1

        composite = (
            v10_norm * W_INTRA_BASE
            + gap_norm * W_INTRA_GAP
            + rvol_norm * W_INTRA_RVOL
            + catalyst_norm * W_INTRA_CATALYST
        )

        # Provisional entry/stop/target — replaced by ORB at Phase 5
        # entry: premarket_close, stop: prev_close - 2*ATR%, t1/t2: 1R/2R
        try:
            daily = get_bars(vp.symbol, start_iso, end_iso, "1d")
            atr_p = float(_atr_pct(daily).iloc[-1]) if not daily.empty else 0.02
        except Exception:
            atr_p = 0.02

        entry_prov = premarket_close
        stop_prov = prev_close * (1.0 - PROVISIONAL_STOP_ATR_MULT * atr_p)
        if stop_prov >= entry_prov:
            stop_prov = entry_prov * 0.97
        r_prov = entry_prov - stop_prov
        if r_prov <= 0:
            skipped.append({"symbol": vp.symbol, "reason": "invalid_provisional_r"})
            continue
        t1_prov = entry_prov + r_prov * PROVISIONAL_TARGET_R_MULT
        t2_prov = entry_prov + r_prov * PROVISIONAL_TARGET_R_MULT * 2

        meta = {
            "selection_path": "intraday_5stack",
            "v10_score": vp.score,
            "v10_meta": vp.score_meta or {},
            "premarket_gap_pct": round(gap_pct, 3),
            "premarket_rvol": round(rvol, 3),
            "premarket_spread_pct": round(spread, 3) if spread else None,
            "premarket_close": round(premarket_close, 4),
            "prev_close": round(prev_close, 4),
            "atr_pct": round(atr_p, 4),
            "catalyst_score": catalyst.score if catalyst else 0,
            "catalyst_summary": catalyst.summary if catalyst else None,
            "catalyst_kind": catalyst.primary_kind.value if catalyst else None,
            "v10_norm": round(v10_norm, 3),
            "gap_norm": round(gap_norm, 3),
            "rvol_norm": round(rvol_norm, 3),
            "catalyst_norm": round(catalyst_norm, 3),
            "regime_score": regime.score,
            "regime_mode": regime.mode,
            "provisional_entry": round(entry_prov, 4),
            "provisional_stop": round(stop_prov, 4),
            "provisional_target_1r": round(t1_prov, 4),
            "provisional_target_2r": round(t2_prov, 4),
            "confirm_status": "watchlist",
            "version": "intraday_v1",
        }

        enriched.append((composite, vp.symbol, vp.sector, meta))

    if skipped:
        logger.info("Intraday skipped (%d): %s", len(skipped), skipped[:5])

    # 4) Rank + sector diversification (reuse v10/v9 logic — penalty after 2)
    enriched.sort(key=lambda x: x[0], reverse=True)
    sector_count: dict[str, int] = {}
    diversified: list[tuple[float, str, str | None, dict]] = []
    for composite, sym, sector, meta in enriched:
        key = sector or "_unk"
        cnt = sector_count.get(key, 0)
        if cnt >= 2:
            penalty = (cnt - 1) * 5.0
            adjusted = composite - penalty
            meta["diversification_penalty"] = penalty
        else:
            adjusted = composite
            meta["diversification_penalty"] = 0.0
        sector_count[key] = cnt + 1
        diversified.append((adjusted, sym, sector, meta))

    diversified.sort(key=lambda x: x[0], reverse=True)

    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(diversified[:top], start=1):
        out.append(PickCandidate(
            system_id="intraday",
            rank=i,
            symbol=sym,
            score=round(composite, 2),
            score_meta=meta,
            sector=sector,
            strategy_tag="day",
        ))
    return out


async def run_integrated_v9(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v9 — Diversification penalty + Anchored VWAP + tighter reject + 30d feedback.

    v8 한계: 5 picks가 한 섹터(반도체)에 집중되어 상관관계 ↑ → drawdown 동시 발생 위험.
    v9 가설:
      1. Sector diversification — 5 picks 중 한 섹터 ≥ 3개면 후순위 picks 페널티 (-5/회)
      2. Anchored VWAP — 5일 anchored VWAP 위 시 +5 (트렌드 컨펌)
      3. Negative reject -1.5% → -1.0% (더 엄격)
      4. Feedback window 15d → 30d, cubic decay (1d ×4, 5d ×2, 30d ×0.3)
      5. v8 streak/OBV/momentum_accel/ER avoidance/VIX-adaptive 유지
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory
    from api.db.models import SystemPickLog, PickOutcome
    from scanner.benchmarks import sector_etf_for, get_benchmark_bars
    from scanner.catalysts import nasdaq_earnings
    from signals.relative_strength import rs_vs_benchmark
    from signals.stage2_trend_template import trend_template_pass
    from sqlalchemy import select

    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v9: defensive regime — long blocked")
        return []
    regime_boost = 1.2 if regime.mode == "aggressive" else 1.0

    vix_high = False
    try:
        vix_value = float(regime.diagnostics.get("vix_value", 0)) if regime.diagnostics.get("vix_value") else None
        if vix_value and vix_value > 25:
            vix_high = True
    except Exception:
        pass

    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)
    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    scanner_by_sym = {sp.symbol: sp for sp in scanner_picks}
    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=400)).isoformat()
    today_d = target_date or date.today()
    streak_lookback_start = today_d - timedelta(days=10)
    feedback_lookback_start = today_d - timedelta(days=30)  # v9: 15→30

    v3_streak: dict[str, int] = {}
    feedback_data: dict[str, list[tuple[date, float]]] = {}
    sess = session
    sess_owns = sess is None
    if sess_owns:
        sess = async_session_factory()
    try:
        stmt = select(SystemPickLog.symbol).where(
            SystemPickLog.system_id == "v3",
            SystemPickLog.pick_date >= streak_lookback_start,
            SystemPickLog.pick_date < today_d,
        )
        for row in (await sess.execute(stmt)).all():
            v3_streak[row[0]] = v3_streak.get(row[0], 0) + 1

        stmt2 = (
            select(SystemPickLog.symbol, SystemPickLog.pick_date, PickOutcome.alpha)
            .join(PickOutcome, PickOutcome.pick_log_id == SystemPickLog.id)
            .where(
                SystemPickLog.pick_date >= feedback_lookback_start,
                SystemPickLog.pick_date < today_d,
                PickOutcome.horizon_days == 5,
            )
        )
        for row in (await sess.execute(stmt2)).all():
            feedback_data.setdefault(row[0], []).append((row[1], float(row[2])))
    except Exception as exc:
        logger.warning("v9 feedback query failed: %s", exc)
    finally:
        if sess_owns:
            await sess.close()

    spy_full = get_benchmark_bars("SPY", lookback_days=400)
    spy_bars_full = _slice_to_date(spy_full, target_date) if (spy_full is not None and target_date) else spy_full
    sector_momentum_cache: dict[str, float] = {}

    def _sector_momentum(sector: str | None) -> float:
        if not sector: return 0.0
        etf = sector_etf_for(sector)
        if not etf: return 0.0
        if etf in sector_momentum_cache: return sector_momentum_cache[etf]
        try:
            etf_full = get_benchmark_bars(etf, lookback_days=30)
            etf_bars = _slice_to_date(etf_full, target_date) if target_date else etf_full
            if etf_bars is None or len(etf_bars) < 6 or spy_bars_full is None or len(spy_bars_full) < 6:
                sector_momentum_cache[etf] = 0.0; return 0.0
            etf_5d = float(etf_bars["close"].iloc[-1] / etf_bars["close"].iloc[-6]) - 1
            spy_5d = float(spy_bars_full["close"].iloc[-1] / spy_bars_full["close"].iloc[-6]) - 1
            sm = etf_5d - spy_5d
            sector_momentum_cache[etf] = sm
            return sm
        except Exception:
            sector_momentum_cache[etf] = 0.0; return 0.0

    def _obv_trend(bars: pd.DataFrame) -> bool:
        if bars is None or len(bars) < 11: return False
        try:
            recent = bars.iloc[-11:]
            close_diff = recent["close"].diff()
            volume = recent["volume"]
            obv = (volume * close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
            return float(obv.iloc[-1]) > float(obv.iloc[0])
        except Exception:
            return False

    def _anchored_vwap_above(bars: pd.DataFrame) -> bool:
        """직전 5일 anchored VWAP 위 시 True."""
        if bars is None or len(bars) < 6:
            return False
        try:
            recent = bars.iloc[-5:]
            tp = (recent["high"] + recent["low"] + recent["close"]) / 3
            vwap = (tp * recent["volume"]).sum() / recent["volume"].sum()
            return float(bars["close"].iloc[-1]) > float(vwap)
        except Exception:
            return False

    def _has_forward_er(symbol: str) -> bool:
        try:
            er_date = nasdaq_earnings._next_earnings_date(symbol)
            if er_date is None: return False
            delta = (er_date - today_d).days
            return 0 <= delta <= 7
        except Exception:
            return False

    def _momentum_acceleration(bars: pd.DataFrame) -> bool:
        if bars is None or len(bars) < 64 or spy_bars_full is None or len(spy_bars_full) < 64:
            return False
        try:
            rs_1m = rs_vs_benchmark(bars, spy_bars_full, 21)
            rs_3m = rs_vs_benchmark(bars, spy_bars_full, 63)
            if rs_1m is None or rs_3m is None: return False
            return rs_1m > rs_3m
        except Exception:
            return False

    def _eval(symbol: str):
        try:
            full_bars = get_bars(symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50: return None
            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad": return None
            last = bars.iloc[-1]; prev = bars.iloc[-2]
            open_loc = compute_open_location(
                open_price=float(last["open"]), pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]), prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk: return None
            try:
                stage2_ok = bool(trend_template_pass(bars).iloc[-1]) if len(bars) >= 252 else False
            except Exception:
                stage2_ok = False
            obv_up = _obv_trend(bars)
            mom_accel = _momentum_acceleration(bars)
            avwap_above = _anchored_vwap_above(bars)
            return ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel, avwap_above
        except Exception:
            return None

    def _earn_mult(scanner_match) -> float:
        if not scanner_match: return 1.0
        phase = (scanner_match.score_meta or {}).get("earnings_phase")
        return 1.25 if phase == "post" else 1.0

    def _streak_bonus(symbol: str) -> tuple[float, int]:
        cnt = v3_streak.get(symbol, 0)
        if cnt >= 5: return 15.0, cnt
        if cnt >= 3: return 10.0, cnt
        return 0.0, cnt

    def _feedback_decay_cubic(symbol: str) -> tuple[float, float, bool]:
        """v9: 30일 window + cubic decay.

        weight: 1d ×4, 2-5d ×2, 6-15d ×1, 16-30d ×0.3
        Reject threshold: -1.0%
        """
        if symbol not in feedback_data:
            return 0.0, 0.0, False
        weighted_sum = 0.0
        weight_total = 0.0
        for pdate, alpha_pct in feedback_data[symbol]:
            age = (today_d - pdate).days
            if age <= 1: w = 4.0
            elif age <= 5: w = 2.0
            elif age <= 15: w = 1.0
            else: w = 0.3
            weighted_sum += alpha_pct * w
            weight_total += w
        if weight_total == 0: return 0.0, 0.0, False
        weighted_avg = weighted_sum / weight_total
        bonus = max(-12.0, min(12.0, weighted_avg * 5.0))  # v9: ±10→±12
        should_reject = weighted_avg < -1.0  # v9: -1.5→-1.0
        return bonus, weighted_avg, should_reject

    all_candidates: list[tuple[float, str, str | None, dict]] = []
    seen: set[str] = set()
    rejected_negative: list[str] = []

    for vp in v3_picks:
        if vp.symbol in seen: continue
        seen.add(vp.symbol)
        if _has_forward_er(vp.symbol): continue
        feedback_b, feedback_avg, reject = _feedback_decay_cubic(vp.symbol)
        if reject:
            rejected_negative.append(vp.symbol); continue
        q = _eval(vp.symbol)
        if q is None: continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel, avwap_above = q
        if vix_high and not (ce.is_compression and ce.is_expansion): continue

        v3_norm = ((vp.score / 100.0) ** 1.3) * 50
        ce_norm = (ce.score / 6.0) * 20 * regime_boost
        if ce.is_compression and ce.is_expansion: ce_norm += 10.0
        if stage2_ok and ce.is_compression: ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 8
        rsi_norm = (rsi.score / 5.0) * 5

        sector = vp.sector
        sector_bonus = 10.0 if (sector and any(p in sector for p in PRIORITY_SECTORS)) else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        sm = scanner_by_sym.get(vp.symbol)
        confluence_bonus = 15.0 if (sm and sm.score >= 4) else 0.0
        stage2_bonus = 8.0 if stage2_ok else 0.0
        em = _earn_mult(sm)

        streak_b, streak_count = _streak_bonus(vp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0
        mom_accel_bonus = 5.0 if mom_accel else 0.0
        avwap_bonus = 5.0 if avwap_above else 0.0  # v9: anchored VWAP

        base = (v3_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
                + confluence_bonus + stage2_bonus + streak_b + obv_bonus + mom_accel_bonus
                + avwap_bonus + feedback_b)
        composite = base * em

        meta = {
            "tier": 1, "selection_path": "v3_priority",
            "v3_score": vp.score, "v3_passed": True,
            "scanner_score": sm.score if sm else 0.0,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "stage2_pass": stage2_ok,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "confluence_bonus": confluence_bonus,
            "stage2_bonus": stage2_bonus, "earnings_multiplier": em,
            "streak_count": streak_count, "streak_bonus": streak_b,
            "obv_up": obv_up, "obv_bonus": obv_bonus,
            "momentum_accel": mom_accel, "mom_accel_bonus": mom_accel_bonus,
            "avwap_above": avwap_above, "avwap_bonus": avwap_bonus,
            "feedback_avg_alpha": round(feedback_avg, 4),
            "feedback_bonus": round(feedback_b, 2),
            "vix_high": vix_high,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v9",
        }
        all_candidates.append((composite, vp.symbol, sector, meta))

    for sp in scanner_picks:
        if sp.symbol in seen: continue
        seen.add(sp.symbol)
        if _has_forward_er(sp.symbol): continue
        feedback_b, feedback_avg, reject = _feedback_decay_cubic(sp.symbol)
        if reject:
            rejected_negative.append(sp.symbol); continue
        q = _eval(sp.symbol)
        if q is None: continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel, avwap_above = q
        if vix_high and not (ce.is_compression and ce.is_expansion): continue

        cnt = 0
        if ce.is_compression: cnt += 1
        if ce.is_expansion: cnt += 1
        if stage2_ok: cnt += 1
        sector = sp.sector
        sector_priority = bool(sector and any(p in sector for p in PRIORITY_SECTORS))
        if sector_priority: cnt += 1
        if open_loc.above_pivot: cnt += 1
        if rsi.grade == "good": cnt += 1
        if obv_up: cnt += 1
        if mom_accel: cnt += 1
        if avwap_above: cnt += 1

        has_ce = ce.is_compression or ce.is_expansion
        if cnt < 3 or not has_ce: continue

        scanner_norm = (sp.score / 5.0) * 25
        ce_norm = (ce.score / 6.0) * 25 * regime_boost
        if ce.is_compression and ce.is_expansion: ce_norm += 8.0
        if stage2_ok and ce.is_compression: ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 12
        rsi_norm = (rsi.score / 5.0) * 8
        stage2_bonus = 12.0 if stage2_ok else 0.0
        sector_bonus = 10.0 if sector_priority else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        em = _earn_mult(sp)

        streak_b, streak_count = _streak_bonus(sp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0
        mom_accel_bonus = 5.0 if mom_accel else 0.0
        avwap_bonus = 5.0 if avwap_above else 0.0

        base = (scanner_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
                + stage2_bonus + streak_b + obv_bonus + mom_accel_bonus + avwap_bonus + feedback_b)
        composite = base * em

        meta = {
            "tier": 2, "selection_path": "scanner_strict_3plus_ce",
            "v3_score": 0, "v3_passed": False,
            "scanner_score": sp.score, "stage2_pass": stage2_ok,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "signals_count": cnt,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "stage2_bonus": stage2_bonus,
            "earnings_multiplier": em, "streak_count": streak_count,
            "streak_bonus": streak_b, "obv_up": obv_up, "obv_bonus": obv_bonus,
            "momentum_accel": mom_accel, "mom_accel_bonus": mom_accel_bonus,
            "avwap_above": avwap_above, "avwap_bonus": avwap_bonus,
            "feedback_avg_alpha": round(feedback_avg, 4),
            "feedback_bonus": round(feedback_b, 2),
            "vix_high": vix_high,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v9",
        }
        all_candidates.append((composite, sp.symbol, sector, meta))

    if rejected_negative:
        logger.info("v9 negative feedback rejected (%d): %s", len(rejected_negative), rejected_negative)

    # v9: Sector diversification penalty — 같은 섹터 후순위 picks에 페널티
    all_candidates.sort(key=lambda x: x[0], reverse=True)

    sector_count: dict[str, int] = {}
    diversified: list[tuple[float, str, str | None, dict]] = []
    for composite, sym, sector, meta in all_candidates:
        cnt_in_sector = sector_count.get(sector or "_unk", 0)
        # 같은 섹터 3번째부터 -5/회 페널티
        if cnt_in_sector >= 2:
            penalty = (cnt_in_sector - 1) * 5.0  # 3번째 -5, 4번째 -10
            adjusted = composite - penalty
            meta["diversification_penalty"] = penalty
        else:
            adjusted = composite
            meta["diversification_penalty"] = 0.0
        sector_count[sector or "_unk"] = cnt_in_sector + 1
        diversified.append((adjusted, sym, sector, meta))

    diversified.sort(key=lambda x: x[0], reverse=True)
    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(diversified[:top], start=1):
        out.append(PickCandidate(
            system_id="integrated", rank=i, symbol=sym,
            score=round(composite, 2), score_meta=meta,
            sector=sector, strategy_tag="swing",
        ))
    return out


async def run_integrated_v8(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v8 — Time-decay feedback + momentum acceleration + negative feedback reject.

    v7 outcome feedback은 flat (15일 평균) — 최근/오래된 동등.
    v8 가설:
      1. Time-decay weighting — 최근(1-3d) ×3, 중간(4-7d) ×1.5, 오래된(8-15d) ×0.5
         → 최근 trend 더 민감
      2. Momentum acceleration — 1m RS > 3m RS = +5 (가속 신호)
      3. Negative feedback reject — recent avg alpha < -1.5% 종목 자동 제외
      4. v7 streak/OBV/ER avoidance/VIX-adaptive 유지
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory
    from api.db.models import SystemPickLog, PickOutcome
    from scanner.benchmarks import sector_etf_for, get_benchmark_bars
    from scanner.catalysts import nasdaq_earnings
    from signals.relative_strength import rs_vs_benchmark
    from signals.stage2_trend_template import trend_template_pass
    from sqlalchemy import select

    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v8: defensive regime — long blocked")
        return []
    regime_boost = 1.2 if regime.mode == "aggressive" else 1.0

    vix_high = False
    try:
        vix_value = float(regime.diagnostics.get("vix_value", 0)) if regime.diagnostics.get("vix_value") else None
        if vix_value and vix_value > 25:
            vix_high = True
    except Exception:
        pass

    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)
    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    scanner_by_sym = {sp.symbol: sp for sp in scanner_picks}
    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=400)).isoformat()  # v8: RS 위해 400일
    today_d = target_date or date.today()
    streak_lookback_start = today_d - timedelta(days=10)
    feedback_lookback_start = today_d - timedelta(days=15)

    # ── Streak + Time-decay outcome feedback ──
    v3_streak: dict[str, int] = {}
    feedback_data: dict[str, list[tuple[date, float]]] = {}  # symbol → [(pick_date, alpha)]
    sess = session
    sess_owns = sess is None
    if sess_owns:
        sess = async_session_factory()
    try:
        stmt = select(SystemPickLog.symbol).where(
            SystemPickLog.system_id == "v3",
            SystemPickLog.pick_date >= streak_lookback_start,
            SystemPickLog.pick_date < today_d,
        )
        for row in (await sess.execute(stmt)).all():
            v3_streak[row[0]] = v3_streak.get(row[0], 0) + 1

        stmt2 = (
            select(SystemPickLog.symbol, SystemPickLog.pick_date, PickOutcome.alpha)
            .join(PickOutcome, PickOutcome.pick_log_id == SystemPickLog.id)
            .where(
                SystemPickLog.pick_date >= feedback_lookback_start,
                SystemPickLog.pick_date < today_d,
                PickOutcome.horizon_days == 5,
            )
        )
        for row in (await sess.execute(stmt2)).all():
            feedback_data.setdefault(row[0], []).append((row[1], float(row[2])))
    except Exception as exc:
        logger.warning("v8 feedback query failed: %s", exc)
    finally:
        if sess_owns:
            await sess.close()

    # ── Sector momentum (v6-7과 동일) ──
    spy_full = get_benchmark_bars("SPY", lookback_days=400)
    spy_bars_full = _slice_to_date(spy_full, target_date) if (spy_full is not None and target_date) else spy_full
    sector_momentum_cache: dict[str, float] = {}

    def _sector_momentum(sector: str | None) -> float:
        if not sector:
            return 0.0
        etf = sector_etf_for(sector)
        if not etf:
            return 0.0
        if etf in sector_momentum_cache:
            return sector_momentum_cache[etf]
        try:
            etf_full = get_benchmark_bars(etf, lookback_days=30)
            etf_bars = _slice_to_date(etf_full, target_date) if target_date else etf_full
            if etf_bars is None or len(etf_bars) < 6 or spy_bars_full is None or len(spy_bars_full) < 6:
                sector_momentum_cache[etf] = 0.0
                return 0.0
            etf_5d = float(etf_bars["close"].iloc[-1] / etf_bars["close"].iloc[-6]) - 1
            spy_5d = float(spy_bars_full["close"].iloc[-1] / spy_bars_full["close"].iloc[-6]) - 1
            sm = etf_5d - spy_5d
            sector_momentum_cache[etf] = sm
            return sm
        except Exception:
            sector_momentum_cache[etf] = 0.0
            return 0.0

    def _obv_trend(bars: pd.DataFrame) -> bool:
        if bars is None or len(bars) < 11:
            return False
        try:
            recent = bars.iloc[-11:]
            close_diff = recent["close"].diff()
            volume = recent["volume"]
            obv = (volume * close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
            return float(obv.iloc[-1]) > float(obv.iloc[0])
        except Exception:
            return False

    def _has_forward_er(symbol: str) -> bool:
        try:
            er_date = nasdaq_earnings._next_earnings_date(symbol)
            if er_date is None:
                return False
            delta = (er_date - today_d).days
            return 0 <= delta <= 7
        except Exception:
            return False

    def _momentum_acceleration(bars: pd.DataFrame) -> bool:
        """1m RS > 3m RS = 모멘텀 가속."""
        if bars is None or len(bars) < 64 or spy_bars_full is None or len(spy_bars_full) < 64:
            return False
        try:
            rs_1m = rs_vs_benchmark(bars, spy_bars_full, 21)
            rs_3m = rs_vs_benchmark(bars, spy_bars_full, 63)
            if rs_1m is None or rs_3m is None:
                return False
            return rs_1m > rs_3m
        except Exception:
            return False

    def _eval(symbol: str):
        try:
            full_bars = get_bars(symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50:
                return None
            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad":
                return None
            last = bars.iloc[-1]
            prev = bars.iloc[-2]
            open_loc = compute_open_location(
                open_price=float(last["open"]),
                pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]),
                prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk:
                return None
            try:
                stage2_ok = bool(trend_template_pass(bars).iloc[-1]) if len(bars) >= 252 else False
            except Exception:
                stage2_ok = False
            obv_up = _obv_trend(bars)
            mom_accel = _momentum_acceleration(bars)
            return ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel
        except Exception:
            return None

    def _earn_mult(scanner_match) -> float:
        if not scanner_match:
            return 1.0
        phase = (scanner_match.score_meta or {}).get("earnings_phase")
        return 1.25 if phase == "post" else 1.0

    def _streak_bonus(symbol: str) -> tuple[float, int]:
        cnt = v3_streak.get(symbol, 0)
        if cnt >= 5:
            return 15.0, cnt
        if cnt >= 3:
            return 10.0, cnt
        return 0.0, cnt

    def _feedback_decay(symbol: str) -> tuple[float, float, bool]:
        """Time-decay outcome feedback.

        weight: 1-3d → 3.0, 4-7d → 1.5, 8-15d → 0.5
        weighted alpha → bonus (max ±10).
        recent avg alpha < -0.015 (-1.5%) → reject 신호 (True).
        """
        if symbol not in feedback_data:
            return 0.0, 0.0, False
        weighted_sum = 0.0
        weight_total = 0.0
        for pdate, alpha_pct in feedback_data[symbol]:
            age_days = (today_d - pdate).days
            if age_days <= 3:
                w = 3.0
            elif age_days <= 7:
                w = 1.5
            else:
                w = 0.5
            weighted_sum += alpha_pct * w
            weight_total += w
        if weight_total == 0:
            return 0.0, 0.0, False
        weighted_avg = weighted_sum / weight_total  # alpha %
        # 0.02 (2%) per +10 → 가중치 ↑
        bonus = max(-10.0, min(10.0, weighted_avg / 100.0 * 500))  # alpha % → 점수
        # NOTE: alpha_pct는 % 단위 (-2.0 = -2%). 500/100 = 5
        # 실제: alpha_pct × 5 = 점수 (alpha 2%면 +10, -1.5%면 -7.5)
        bonus = max(-10.0, min(10.0, weighted_avg * 5.0))
        # Negative reject: weighted_avg < -1.5%
        should_reject = weighted_avg < -1.5
        return bonus, weighted_avg, should_reject

    all_candidates: list[tuple[float, str, str | None, dict]] = []
    seen: set[str] = set()
    rejected_negative: list[str] = []

    for vp in v3_picks:
        if vp.symbol in seen:
            continue
        seen.add(vp.symbol)

        if _has_forward_er(vp.symbol):
            continue

        # v8: Negative feedback reject
        feedback_b, feedback_avg, reject = _feedback_decay(vp.symbol)
        if reject:
            rejected_negative.append(vp.symbol)
            continue

        q = _eval(vp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel = q

        if vix_high and not (ce.is_compression and ce.is_expansion):
            continue

        v3_norm = ((vp.score / 100.0) ** 1.3) * 50
        ce_norm = (ce.score / 6.0) * 20 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 10.0
        if stage2_ok and ce.is_compression:
            ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 8
        rsi_norm = (rsi.score / 5.0) * 5

        sector = vp.sector
        sector_bonus = 10.0 if (sector and any(p in sector for p in PRIORITY_SECTORS)) else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        sm = scanner_by_sym.get(vp.symbol)
        confluence_bonus = 15.0 if (sm and sm.score >= 4) else 0.0
        stage2_bonus = 8.0 if stage2_ok else 0.0
        em = _earn_mult(sm)

        streak_b, streak_count = _streak_bonus(vp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0
        mom_accel_bonus = 5.0 if mom_accel else 0.0  # v8: 모멘텀 가속

        base = (
            v3_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
            + confluence_bonus + stage2_bonus + streak_b + obv_bonus + mom_accel_bonus + feedback_b
        )
        composite = base * em

        meta = {
            "tier": 1, "selection_path": "v3_priority",
            "v3_score": vp.score, "v3_passed": True,
            "scanner_score": sm.score if sm else 0.0,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "stage2_pass": stage2_ok,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "confluence_bonus": confluence_bonus,
            "stage2_bonus": stage2_bonus, "earnings_multiplier": em,
            "streak_count": streak_count, "streak_bonus": streak_b,
            "obv_up": obv_up, "obv_bonus": obv_bonus,
            "momentum_accel": mom_accel, "mom_accel_bonus": mom_accel_bonus,
            "feedback_avg_alpha": round(feedback_avg, 4),
            "feedback_bonus": round(feedback_b, 2),
            "vix_high": vix_high,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v8",
        }
        all_candidates.append((composite, vp.symbol, sector, meta))

    for sp in scanner_picks:
        if sp.symbol in seen:
            continue
        seen.add(sp.symbol)
        if _has_forward_er(sp.symbol):
            continue

        feedback_b, feedback_avg, reject = _feedback_decay(sp.symbol)
        if reject:
            rejected_negative.append(sp.symbol)
            continue

        q = _eval(sp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up, mom_accel = q

        if vix_high and not (ce.is_compression and ce.is_expansion):
            continue

        cnt = 0
        if ce.is_compression: cnt += 1
        if ce.is_expansion: cnt += 1
        if stage2_ok: cnt += 1
        sector = sp.sector
        sector_priority = bool(sector and any(p in sector for p in PRIORITY_SECTORS))
        if sector_priority: cnt += 1
        if open_loc.above_pivot: cnt += 1
        if rsi.grade == "good": cnt += 1
        if obv_up: cnt += 1
        if mom_accel: cnt += 1  # v8: 가속도 signal로 카운트

        has_ce = ce.is_compression or ce.is_expansion
        if cnt < 3 or not has_ce:
            continue

        scanner_norm = (sp.score / 5.0) * 25
        ce_norm = (ce.score / 6.0) * 25 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 8.0
        if stage2_ok and ce.is_compression:
            ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 12
        rsi_norm = (rsi.score / 5.0) * 8
        stage2_bonus = 12.0 if stage2_ok else 0.0
        sector_bonus = 10.0 if sector_priority else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        em = _earn_mult(sp)

        streak_b, streak_count = _streak_bonus(sp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0
        mom_accel_bonus = 5.0 if mom_accel else 0.0

        base = (
            scanner_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
            + stage2_bonus + streak_b + obv_bonus + mom_accel_bonus + feedback_b
        )
        composite = base * em

        meta = {
            "tier": 2, "selection_path": "scanner_strict_3plus_ce",
            "v3_score": 0, "v3_passed": False,
            "scanner_score": sp.score, "stage2_pass": stage2_ok,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "signals_count": cnt,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "stage2_bonus": stage2_bonus,
            "earnings_multiplier": em, "streak_count": streak_count,
            "streak_bonus": streak_b, "obv_up": obv_up, "obv_bonus": obv_bonus,
            "momentum_accel": mom_accel, "mom_accel_bonus": mom_accel_bonus,
            "feedback_avg_alpha": round(feedback_avg, 4),
            "feedback_bonus": round(feedback_b, 2),
            "vix_high": vix_high,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v8",
        }
        all_candidates.append((composite, sp.symbol, sector, meta))

    if rejected_negative:
        logger.info("v8 negative feedback rejected: %s", rejected_negative)

    all_candidates.sort(key=lambda x: x[0], reverse=True)
    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(all_candidates[:top], start=1):
        out.append(PickCandidate(
            system_id="integrated", rank=i, symbol=sym,
            score=round(composite, 2), score_meta=meta,
            sector=sector, strategy_tag="swing",
        ))
    return out


async def run_integrated_v7(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v7 — outcome feedback + forward earnings avoidance + VIX-adaptive scoring.

    v6 한계: 5d alpha 3.90% < v3 standalone 4.38% (gap 0.48%).
    v7 가설:
      1. Recent outcome feedback (+/- 8) — 직전 10일 picks의 5d alpha 누적
         → 좋은 종목 강화, 나쁜 종목 페널티
      2. Forward ER avoidance — 향후 5거래일 내 실적 발표 종목 제외 (binary risk)
      3. VIX-adaptive — VIX > 25 시 compression AND expansion 둘 다 필수 (v6: OR)
      4. Earnings phase post는 ×1.25 (v6: ×1.2) — PEAD 더 강조
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory
    from api.db.models import SystemPickLog, PickOutcome
    from scanner.benchmarks import sector_etf_for, get_benchmark_bars
    from scanner.catalysts import nasdaq_earnings
    from signals.stage2_trend_template import trend_template_pass
    from sqlalchemy import select, and_

    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v7: defensive regime — long blocked")
        return []
    regime_boost = 1.2 if regime.mode == "aggressive" else 1.0

    # VIX 추출 — adaptive scoring 결정
    vix_high = False
    try:
        vix_value = float(regime.diagnostics.get("vix_value", 0)) if regime.diagnostics.get("vix_value") else None
        if vix_value and vix_value > 25:
            vix_high = True
    except Exception:
        pass

    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)
    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    scanner_by_sym = {sp.symbol: sp for sp in scanner_picks}
    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=120)).isoformat()
    today_d = target_date or date.today()
    streak_lookback_start = today_d - timedelta(days=10)
    feedback_lookback_start = today_d - timedelta(days=15)

    # ── Streak + Recent outcome feedback 조회 ──
    v3_streak: dict[str, int] = {}
    outcome_feedback: dict[str, float] = {}  # symbol → avg 5d alpha of recent picks
    sess = session
    sess_owns = sess is None
    if sess_owns:
        sess = async_session_factory()
    try:
        # Streak (v3 picks 빈도)
        stmt = select(SystemPickLog.symbol).where(
            SystemPickLog.system_id == "v3",
            SystemPickLog.pick_date >= streak_lookback_start,
            SystemPickLog.pick_date < today_d,
        )
        for row in (await sess.execute(stmt)).all():
            v3_streak[row[0]] = v3_streak.get(row[0], 0) + 1

        # Outcome feedback — 직전 15일 picks의 5d outcome 알파 평균
        stmt2 = (
            select(SystemPickLog.symbol, PickOutcome.alpha)
            .join(PickOutcome, PickOutcome.pick_log_id == SystemPickLog.id)
            .where(
                SystemPickLog.pick_date >= feedback_lookback_start,
                SystemPickLog.pick_date < today_d,
                PickOutcome.horizon_days == 5,
            )
        )
        sym_alphas: dict[str, list[float]] = {}
        for row in (await sess.execute(stmt2)).all():
            sym_alphas.setdefault(row[0], []).append(float(row[1]))
        for sym, alphas in sym_alphas.items():
            outcome_feedback[sym] = sum(alphas) / len(alphas)
    except Exception as exc:
        logger.warning("v7 feedback query failed: %s", exc)
    finally:
        if sess_owns:
            await sess.close()

    # ── Sector momentum (v6과 동일) ──
    spy_full = get_benchmark_bars("SPY", lookback_days=30)
    spy_bars = _slice_to_date(spy_full, target_date) if (spy_full is not None and target_date) else spy_full
    sector_momentum_cache: dict[str, float] = {}

    def _sector_momentum(sector: str | None) -> float:
        if not sector:
            return 0.0
        etf = sector_etf_for(sector)
        if not etf:
            return 0.0
        if etf in sector_momentum_cache:
            return sector_momentum_cache[etf]
        try:
            etf_full = get_benchmark_bars(etf, lookback_days=30)
            etf_bars = _slice_to_date(etf_full, target_date) if target_date else etf_full
            if etf_bars is None or len(etf_bars) < 6 or spy_bars is None or len(spy_bars) < 6:
                sector_momentum_cache[etf] = 0.0
                return 0.0
            etf_5d = float(etf_bars["close"].iloc[-1] / etf_bars["close"].iloc[-6]) - 1
            spy_5d = float(spy_bars["close"].iloc[-1] / spy_bars["close"].iloc[-6]) - 1
            sm = etf_5d - spy_5d
            sector_momentum_cache[etf] = sm
            return sm
        except Exception:
            sector_momentum_cache[etf] = 0.0
            return 0.0

    def _obv_trend(bars: pd.DataFrame) -> bool:
        if bars is None or len(bars) < 11:
            return False
        try:
            recent = bars.iloc[-11:]
            close_diff = recent["close"].diff()
            volume = recent["volume"]
            obv = (volume * close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
            return float(obv.iloc[-1]) > float(obv.iloc[0])
        except Exception:
            return False

    def _has_forward_er(symbol: str) -> bool:
        """향후 5거래일(약 7달력일) 내 실적 발표 예정인가."""
        try:
            er_date = nasdaq_earnings._next_earnings_date(symbol)
            if er_date is None:
                return False
            delta = (er_date - today_d).days
            return 0 <= delta <= 7  # 1주일 내 ER
        except Exception:
            return False

    def _eval(symbol: str):
        try:
            full_bars = get_bars(symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50:
                return None
            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad":
                return None
            last = bars.iloc[-1]
            prev = bars.iloc[-2]
            open_loc = compute_open_location(
                open_price=float(last["open"]),
                pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]),
                prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk:
                return None
            try:
                stage2_ok = bool(trend_template_pass(bars).iloc[-1]) if len(bars) >= 252 else False
            except Exception:
                stage2_ok = False
            obv_up = _obv_trend(bars)
            return ce, rsi, open_loc, bars, stage2_ok, obv_up
        except Exception:
            return None

    def _earn_mult(scanner_match) -> float:
        if not scanner_match:
            return 1.0
        phase = (scanner_match.score_meta or {}).get("earnings_phase")
        return 1.25 if phase == "post" else 1.0  # v7: 1.2 → 1.25

    def _streak_bonus(symbol: str) -> tuple[float, int]:
        cnt = v3_streak.get(symbol, 0)
        if cnt >= 5:
            return 15.0, cnt
        if cnt >= 3:
            return 10.0, cnt
        return 0.0, cnt

    def _feedback_bonus(symbol: str) -> tuple[float, float]:
        """Recent outcome alpha 평균 → bonus/penalty.

        +2% alpha → +8 / -2% alpha → -8 (max ±8)
        """
        if symbol not in outcome_feedback:
            return 0.0, 0.0
        avg_alpha = outcome_feedback[symbol]
        # 0.02(2%)당 8점, clamp [-8, 8]
        bonus = max(-8.0, min(8.0, avg_alpha * 400))
        return bonus, avg_alpha

    all_candidates: list[tuple[float, str, str | None, dict]] = []
    seen: set[str] = set()

    # v3 picks
    for vp in v3_picks:
        if vp.symbol in seen:
            continue
        seen.add(vp.symbol)

        # v7: Forward ER 회피
        if _has_forward_er(vp.symbol):
            logger.debug("v7 skip %s — forward ER within 7d", vp.symbol)
            continue

        q = _eval(vp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up = q

        # v7 VIX-adaptive: VIX > 25면 compression AND expansion 둘 다 필수
        if vix_high and not (ce.is_compression and ce.is_expansion):
            continue

        v3_norm = ((vp.score / 100.0) ** 1.3) * 50
        ce_norm = (ce.score / 6.0) * 20 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 10.0
        if stage2_ok and ce.is_compression:
            ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 8
        rsi_norm = (rsi.score / 5.0) * 5

        sector = vp.sector
        sector_bonus = 10.0 if (sector and any(p in sector for p in PRIORITY_SECTORS)) else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        sm = scanner_by_sym.get(vp.symbol)
        confluence_bonus = 15.0 if (sm and sm.score >= 4) else 0.0
        stage2_bonus = 8.0 if stage2_ok else 0.0
        em = _earn_mult(sm)

        streak_b, streak_count = _streak_bonus(vp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0
        feedback_b, feedback_avg = _feedback_bonus(vp.symbol)

        base = (
            v3_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
            + confluence_bonus + stage2_bonus + streak_b + obv_bonus + feedback_b
        )
        composite = base * em

        meta = {
            "tier": 1, "selection_path": "v3_priority",
            "v3_score": vp.score, "v3_passed": True,
            "scanner_score": sm.score if sm else 0.0,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "stage2_pass": stage2_ok,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "confluence_bonus": confluence_bonus,
            "stage2_bonus": stage2_bonus, "earnings_multiplier": em,
            "streak_count": streak_count, "streak_bonus": streak_b,
            "obv_up": obv_up, "obv_bonus": obv_bonus,
            "feedback_avg_alpha": round(feedback_avg, 4),
            "feedback_bonus": round(feedback_b, 2),
            "vix_high": vix_high,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v7",
        }
        all_candidates.append((composite, vp.symbol, sector, meta))

    # scanner picks (3+ quality + CE 필수)
    for sp in scanner_picks:
        if sp.symbol in seen:
            continue
        seen.add(sp.symbol)

        if _has_forward_er(sp.symbol):
            continue

        q = _eval(sp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up = q

        if vix_high and not (ce.is_compression and ce.is_expansion):
            continue

        cnt = 0
        if ce.is_compression: cnt += 1
        if ce.is_expansion: cnt += 1
        if stage2_ok: cnt += 1
        sector = sp.sector
        sector_priority = bool(sector and any(p in sector for p in PRIORITY_SECTORS))
        if sector_priority: cnt += 1
        if open_loc.above_pivot: cnt += 1
        if rsi.grade == "good": cnt += 1
        if obv_up: cnt += 1

        has_ce = ce.is_compression or ce.is_expansion
        if cnt < 3 or not has_ce:
            continue

        scanner_norm = (sp.score / 5.0) * 25
        ce_norm = (ce.score / 6.0) * 25 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 8.0
        if stage2_ok and ce.is_compression:
            ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 12
        rsi_norm = (rsi.score / 5.0) * 8
        stage2_bonus = 12.0 if stage2_ok else 0.0
        sector_bonus = 10.0 if sector_priority else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        em = _earn_mult(sp)

        streak_b, streak_count = _streak_bonus(sp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0
        feedback_b, feedback_avg = _feedback_bonus(sp.symbol)

        base = (
            scanner_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
            + stage2_bonus + streak_b + obv_bonus + feedback_b
        )
        composite = base * em

        meta = {
            "tier": 2, "selection_path": "scanner_strict_3plus_ce",
            "v3_score": 0, "v3_passed": False,
            "scanner_score": sp.score, "stage2_pass": stage2_ok,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "signals_count": cnt,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "stage2_bonus": stage2_bonus,
            "earnings_multiplier": em, "streak_count": streak_count,
            "streak_bonus": streak_b, "obv_up": obv_up, "obv_bonus": obv_bonus,
            "feedback_avg_alpha": round(feedback_avg, 4),
            "feedback_bonus": round(feedback_b, 2),
            "vix_high": vix_high,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v7",
        }
        all_candidates.append((composite, sp.symbol, sector, meta))

    all_candidates.sort(key=lambda x: x[0], reverse=True)
    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(all_candidates[:top], start=1):
        out.append(PickCandidate(
            system_id="integrated", rank=i, symbol=sym,
            score=round(composite, 2), score_meta=meta,
            sector=sector, strategy_tag="swing",
        ))
    return out


async def run_integrated_v6(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v6 — v3 streak bonus + OBV trend + Tier 2 compression 필수.

    v5 한계: 5d alpha 3.91% < v3 standalone 4.38% (gap 0.47%).
    v6 가설:
      1. Multi-day v3 streak bonus +10 — 최근 5거래일 중 v3 picks에 3+회 등장 시
         (consistent strong setup 신호 — 단발성 노이즈 제거)
      2. OBV trend bonus +5 — 직전 10일 OBV 상승 (Money Flow accumulation)
      3. Tier 2 compression OR expansion 필수 — quality signals 3+ AND (compression OR expansion)
         → 가짜 setup 추가 제거
      4. v3 streak 5일 연속 시 추가 +5 (강한 confluence)
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory
    from api.db.models import SystemPickLog
    from scanner.benchmarks import sector_etf_for, get_benchmark_bars
    from signals.stage2_trend_template import trend_template_pass
    from sqlalchemy import select

    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v6: defensive regime — long blocked")
        return []
    regime_boost = 1.2 if regime.mode == "aggressive" else 1.0

    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)
    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    scanner_by_sym = {sp.symbol: sp for sp in scanner_picks}
    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=120)).isoformat()
    today_d = target_date or date.today()
    streak_lookback_start = today_d - timedelta(days=10)  # 5 거래일 + 주말

    # ── Multi-day v3 streak 조회 ──
    v3_streak: dict[str, int] = {}  # symbol → past 5 trading days appearance count
    streak_session = session
    streak_owns = streak_session is None
    if streak_owns:
        streak_session = async_session_factory()
    try:
        stmt = select(SystemPickLog.symbol, SystemPickLog.pick_date).where(
            SystemPickLog.system_id == "v3",
            SystemPickLog.pick_date >= streak_lookback_start,
            SystemPickLog.pick_date < today_d,
        )
        result = await streak_session.execute(stmt)
        for row in result:
            v3_streak[row[0]] = v3_streak.get(row[0], 0) + 1
    except Exception as exc:
        logger.warning("v3 streak query failed: %s", exc)
    finally:
        if streak_owns:
            await streak_session.close()

    # ── Sector momentum (v5와 동일) ──
    spy_full = get_benchmark_bars("SPY", lookback_days=30)
    spy_bars = _slice_to_date(spy_full, target_date) if (spy_full is not None and target_date) else spy_full
    sector_momentum_cache: dict[str, float] = {}

    def _sector_momentum(sector: str | None) -> float:
        if not sector:
            return 0.0
        etf = sector_etf_for(sector)
        if not etf:
            return 0.0
        if etf in sector_momentum_cache:
            return sector_momentum_cache[etf]
        try:
            etf_full = get_benchmark_bars(etf, lookback_days=30)
            etf_bars = _slice_to_date(etf_full, target_date) if target_date else etf_full
            if etf_bars is None or len(etf_bars) < 6 or spy_bars is None or len(spy_bars) < 6:
                sector_momentum_cache[etf] = 0.0
                return 0.0
            etf_5d = float(etf_bars["close"].iloc[-1] / etf_bars["close"].iloc[-6]) - 1
            spy_5d = float(spy_bars["close"].iloc[-1] / spy_bars["close"].iloc[-6]) - 1
            sm = etf_5d - spy_5d
            sector_momentum_cache[etf] = sm
            return sm
        except Exception:
            sector_momentum_cache[etf] = 0.0
            return 0.0

    def _obv_trend(bars: pd.DataFrame) -> bool:
        """직전 10일 OBV slope > 0 — accumulation 신호."""
        if bars is None or len(bars) < 11:
            return False
        try:
            recent = bars.iloc[-11:]
            close_diff = recent["close"].diff()
            volume = recent["volume"]
            obv = (volume * close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
            # 단순 추세: 마지막 OBV > 첫 OBV
            return float(obv.iloc[-1]) > float(obv.iloc[0])
        except Exception:
            return False

    def _eval(symbol: str):
        try:
            full_bars = get_bars(symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50:
                return None
            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad":
                return None
            last = bars.iloc[-1]
            prev = bars.iloc[-2]
            open_loc = compute_open_location(
                open_price=float(last["open"]),
                pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]),
                prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk:
                return None
            try:
                stage2_ok = bool(trend_template_pass(bars).iloc[-1]) if len(bars) >= 252 else False
            except Exception:
                stage2_ok = False
            obv_up = _obv_trend(bars)
            return ce, rsi, open_loc, bars, stage2_ok, obv_up
        except Exception:
            return None

    def _earn_mult(scanner_match) -> float:
        if not scanner_match:
            return 1.0
        phase = (scanner_match.score_meta or {}).get("earnings_phase")
        return 1.2 if phase == "post" else 1.0

    def _streak_bonus(symbol: str) -> tuple[float, int]:
        """v3 streak count → bonus."""
        cnt = v3_streak.get(symbol, 0)
        if cnt >= 5:
            return 15.0, cnt  # 5+ 연속 = 강한 confluence
        if cnt >= 3:
            return 10.0, cnt  # 3+ 연속 = 중간 confluence
        return 0.0, cnt

    all_candidates: list[tuple[float, str, str | None, dict]] = []
    seen: set[str] = set()

    # v3 picks
    for vp in v3_picks:
        if vp.symbol in seen:
            continue
        seen.add(vp.symbol)
        q = _eval(vp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up = q

        v3_norm = ((vp.score / 100.0) ** 1.3) * 50
        ce_norm = (ce.score / 6.0) * 20 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 10.0
        if stage2_ok and ce.is_compression:
            ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 8
        rsi_norm = (rsi.score / 5.0) * 5

        sector = vp.sector
        sector_bonus = 10.0 if (sector and any(p in sector for p in PRIORITY_SECTORS)) else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        sm = scanner_by_sym.get(vp.symbol)
        confluence_bonus = 15.0 if (sm and sm.score >= 4) else 0.0
        stage2_bonus = 8.0 if stage2_ok else 0.0
        em = _earn_mult(sm)

        # v6 신규: streak bonus + OBV
        streak_b, streak_count = _streak_bonus(vp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0

        base = (
            v3_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
            + confluence_bonus + stage2_bonus + streak_b + obv_bonus
        )
        composite = base * em

        meta = {
            "tier": 1, "selection_path": "v3_priority",
            "v3_score": vp.score, "v3_passed": True,
            "scanner_score": sm.score if sm else 0.0,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "stage2_pass": stage2_ok,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "confluence_bonus": confluence_bonus,
            "stage2_bonus": stage2_bonus, "earnings_multiplier": em,
            "streak_count": streak_count, "streak_bonus": streak_b,
            "obv_up": obv_up, "obv_bonus": obv_bonus,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v6",
        }
        all_candidates.append((composite, vp.symbol, sector, meta))

    # scanner picks (3+ quality + compression OR expansion 필수)
    for sp in scanner_picks:
        if sp.symbol in seen:
            continue
        seen.add(sp.symbol)
        q = _eval(sp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok, obv_up = q

        cnt = 0
        if ce.is_compression: cnt += 1
        if ce.is_expansion: cnt += 1
        if stage2_ok: cnt += 1
        sector = sp.sector
        sector_priority = bool(sector and any(p in sector for p in PRIORITY_SECTORS))
        if sector_priority: cnt += 1
        if open_loc.above_pivot: cnt += 1
        if rsi.grade == "good": cnt += 1
        if obv_up: cnt += 1

        # v6: 3+ signals AND (compression OR expansion) 필수
        has_ce = ce.is_compression or ce.is_expansion
        if cnt < 3 or not has_ce:
            continue

        scanner_norm = (sp.score / 5.0) * 25
        ce_norm = (ce.score / 6.0) * 25 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 8.0
        if stage2_ok and ce.is_compression:
            ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 12
        rsi_norm = (rsi.score / 5.0) * 8
        stage2_bonus = 12.0 if stage2_ok else 0.0
        sector_bonus = 10.0 if sector_priority else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        em = _earn_mult(sp)

        # Streak/OBV
        streak_b, streak_count = _streak_bonus(sp.symbol)
        obv_bonus = 5.0 if obv_up else 0.0

        base = (
            scanner_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus
            + stage2_bonus + streak_b + obv_bonus
        )
        composite = base * em

        meta = {
            "tier": 2, "selection_path": "scanner_strict_3plus_ce",
            "v3_score": 0, "v3_passed": False,
            "scanner_score": sp.score, "stage2_pass": stage2_ok,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade, "signals_count": cnt,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "stage2_bonus": stage2_bonus,
            "earnings_multiplier": em, "streak_count": streak_count,
            "streak_bonus": streak_b, "obv_up": obv_up, "obv_bonus": obv_bonus,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v6",
        }
        all_candidates.append((composite, sp.symbol, sector, meta))

    all_candidates.sort(key=lambda x: x[0], reverse=True)
    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(all_candidates[:top], start=1):
        out.append(PickCandidate(
            system_id="integrated", rank=i, symbol=sym,
            score=round(composite, 2), score_meta=meta,
            sector=sector, strategy_tag="swing",
        ))
    return out


async def run_integrated_v5(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v5 — Composite 동등 경쟁 + earnings multiplier + sector momentum + Tier 2 3+.

    v4 한계: Tier 1/2 strict order로 Tier 2 거의 미활성 → integrated가 v3 standalone과 동일.
    v5 개선:
      1. Tier 1/2 strict 폐지 — 모두 composite 단일 정렬로 fair competition
      2. Tier 2 quality signals 3+ 필수 (v4: 2+) → Tier 2 평균 quality ↑
      3. Earnings post phase ×1.2 multiplier — PEAD +2.56% 알파 강조
      4. Sector momentum bonus (+5) — 섹터 ETF 5일 RS > 0.5%
      5. Stage 2 + compression 동시 = 추가 +5 (Minervini 골든)
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory
    from scanner.benchmarks import sector_etf_for, get_benchmark_bars
    from signals.stage2_trend_template import trend_template_pass

    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v5: defensive regime — long blocked")
        return []
    regime_boost = 1.2 if regime.mode == "aggressive" else 1.0

    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)
    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    scanner_by_sym = {sp.symbol: sp for sp in scanner_picks}
    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=120)).isoformat()

    spy_full = get_benchmark_bars("SPY", lookback_days=30)
    spy_bars = _slice_to_date(spy_full, target_date) if (spy_full is not None and target_date) else spy_full
    sector_momentum_cache: dict[str, float] = {}

    def _sector_momentum(sector: str | None) -> float:
        if not sector:
            return 0.0
        etf = sector_etf_for(sector)
        if not etf:
            return 0.0
        if etf in sector_momentum_cache:
            return sector_momentum_cache[etf]
        try:
            etf_full = get_benchmark_bars(etf, lookback_days=30)
            etf_bars = _slice_to_date(etf_full, target_date) if target_date else etf_full
            if etf_bars is None or len(etf_bars) < 6 or spy_bars is None or len(spy_bars) < 6:
                sector_momentum_cache[etf] = 0.0
                return 0.0
            etf_5d = float(etf_bars["close"].iloc[-1] / etf_bars["close"].iloc[-6]) - 1
            spy_5d = float(spy_bars["close"].iloc[-1] / spy_bars["close"].iloc[-6]) - 1
            sector_rs = etf_5d - spy_5d
            sector_momentum_cache[etf] = sector_rs
            return sector_rs
        except Exception:
            sector_momentum_cache[etf] = 0.0
            return 0.0

    def _eval(symbol: str):
        try:
            full_bars = get_bars(symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50:
                return None
            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad":
                return None
            last = bars.iloc[-1]
            prev = bars.iloc[-2]
            open_loc = compute_open_location(
                open_price=float(last["open"]),
                pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]),
                prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk:
                return None
            try:
                stage2_ok = bool(trend_template_pass(bars).iloc[-1]) if len(bars) >= 252 else False
            except Exception:
                stage2_ok = False
            return ce, rsi, open_loc, bars, stage2_ok
        except Exception:
            return None

    def _earn_mult(scanner_match) -> float:
        if not scanner_match:
            return 1.0
        phase = (scanner_match.score_meta or {}).get("earnings_phase")
        return 1.2 if phase == "post" else 1.0

    all_candidates: list[tuple[float, str, str | None, dict]] = []
    seen: set[str] = set()

    # v3 picks
    for vp in v3_picks:
        if vp.symbol in seen:
            continue
        seen.add(vp.symbol)
        q = _eval(vp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok = q
        v3_norm = ((vp.score / 100.0) ** 1.3) * 50
        ce_norm = (ce.score / 6.0) * 20 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 10.0
        if stage2_ok and ce.is_compression:
            ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 8
        rsi_norm = (rsi.score / 5.0) * 5
        sector = vp.sector
        sector_bonus = 10.0 if (sector and any(p in sector for p in PRIORITY_SECTORS)) else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        sm = scanner_by_sym.get(vp.symbol)
        confluence_bonus = 15.0 if (sm and sm.score >= 4) else 0.0
        stage2_bonus = 8.0 if stage2_ok else 0.0
        em = _earn_mult(sm)
        base = v3_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus + confluence_bonus + stage2_bonus
        composite = base * em
        meta = {
            "tier": 1, "selection_path": "v3_priority",
            "v3_score": vp.score, "v3_passed": True,
            "scanner_score": sm.score if sm else 0.0,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade,
            "rsi_value": round(rsi.rsi_value, 2),
            "rsi_score": rsi.score,
            "stage2_pass": stage2_ok,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "confluence_bonus": confluence_bonus,
            "stage2_bonus": stage2_bonus, "earnings_multiplier": em,
            "regime_score": regime.score, "regime_mode": regime.mode,
            "version": "v5",
        }
        all_candidates.append((composite, vp.symbol, sector, meta))

    # scanner picks (3+ quality 필수)
    for sp in scanner_picks:
        if sp.symbol in seen:
            continue
        seen.add(sp.symbol)
        q = _eval(sp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok = q
        cnt = 0
        if ce.is_compression: cnt += 1
        if ce.is_expansion: cnt += 1
        if stage2_ok: cnt += 1
        sector = sp.sector
        sector_priority = bool(sector and any(p in sector for p in PRIORITY_SECTORS))
        if sector_priority: cnt += 1
        if open_loc.above_pivot: cnt += 1
        if rsi.grade == "good": cnt += 1
        if cnt < 3:
            continue
        scanner_norm = (sp.score / 5.0) * 25
        ce_norm = (ce.score / 6.0) * 25 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 8.0
        if stage2_ok and ce.is_compression:
            ce_norm += 5.0
        ol_norm = (open_loc.score / 5.0) * 12
        rsi_norm = (rsi.score / 5.0) * 8
        stage2_bonus = 12.0 if stage2_ok else 0.0
        sector_bonus = 10.0 if sector_priority else 0.0
        sec_mom = _sector_momentum(sector)
        sector_mom_bonus = 5.0 if sec_mom > 0.005 else 0.0
        em = _earn_mult(sp)
        base = scanner_norm + ce_norm + ol_norm + rsi_norm + sector_bonus + sector_mom_bonus + stage2_bonus
        composite = base * em
        meta = {
            "tier": 2, "selection_path": "scanner_strict_3plus",
            "v3_score": 0, "v3_passed": False,
            "scanner_score": sp.score, "stage2_pass": stage2_ok,
            "compression": ce.is_compression, "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score, "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade,
            "rsi_value": round(rsi.rsi_value, 2),
            "rsi_score": rsi.score,
            "signals_count": cnt,
            "sector_bonus": sector_bonus, "sector_momentum": round(sec_mom, 4),
            "sector_mom_bonus": sector_mom_bonus, "stage2_bonus": stage2_bonus,
            "earnings_multiplier": em, "regime_score": regime.score,
            "regime_mode": regime.mode, "version": "v5",
        }
        all_candidates.append((composite, sp.symbol, sector, meta))

    all_candidates.sort(key=lambda x: x[0], reverse=True)
    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(all_candidates[:top], start=1):
        out.append(PickCandidate(
            system_id="integrated", rank=i, symbol=sym,
            score=round(composite, 2), score_meta=meta,
            sector=sector, strategy_tag="swing",
        ))
    return out


async def run_integrated_v4(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v4 — v3 + PEAD bonus + golden setup amplifier + Tier 2 엄격 (2+ signals).

    v3 한계: 5d alpha 1.93% < v3 standalone 4.38% (sample 2배 → 평균 희석).
    v4 가설:
      1. PEAD bonus (+12) — scanner의 검증된 +2.56% earnings 알파 leverage
      2. Golden setup amplifier (+10 추가) — compression AND expansion 동시 발생 시
      3. Tier 2 엄격 — 2+ quality signals 필수 (이전: 1개+) → 약한 setup 제거
      4. v3_score quadratic 가중 (^1.3) — top v3 picks (score 80+) 더 강조
      5. Aggressive regime → ce_norm ×1.2 boost
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory
    from signals.stage2_trend_template import trend_template_pass

    # 1) Regime gate
    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v4: defensive regime — long blocked")
        return []
    regime_boost = 1.2 if regime.mode == "aggressive" else 1.0

    # 2) Universe
    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)

    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    scanner_by_sym = {sp.symbol: sp for sp in scanner_picks}
    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=120)).isoformat()

    def _evaluate_quality(symbol: str):
        try:
            full_bars = get_bars(symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50:
                return None
            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad":
                return None
            last = bars.iloc[-1]
            prev = bars.iloc[-2]
            open_loc = compute_open_location(
                open_price=float(last["open"]),
                pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]),
                prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk:
                return None
            try:
                stage2_ok = bool(trend_template_pass(bars).iloc[-1]) if len(bars) >= 252 else False
            except Exception:
                stage2_ok = False
            return ce, rsi, open_loc, bars, stage2_ok
        except Exception as exc:
            logger.warning("v4 quality eval failed for %s: %s", symbol, exc)
            return None

    def _pead_bonus(scanner_match) -> float:
        """scanner picks의 earnings_phase에서 PEAD 보너스 추출."""
        if not scanner_match:
            return 0.0
        phase = (scanner_match.score_meta or {}).get("earnings_phase")
        if phase == "post":
            return 12.0  # PEAD +2.56% 알파 leverage
        return 0.0

    # 3) Tier 1: v3 priority with PEAD + golden setup
    tier1: list[tuple[float, str, str | None, dict]] = []
    for vp in v3_picks:
        q = _evaluate_quality(vp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, _bars, stage2_ok = q

        # v3_score quadratic — top v3 (80+) 더 강조
        v3_normalized = vp.score / 100.0
        v3_score_norm = (v3_normalized ** 1.3) * 50

        # Quality with regime boost
        ce_norm = (ce.score / 6.0) * 20 * regime_boost
        # Golden setup amplifier — compression AND expansion 동시
        if ce.is_compression and ce.is_expansion:
            ce_norm += 10.0  # 골든 setup
        ol_norm = (open_loc.score / 5.0) * 8
        rsi_norm = (rsi.score / 5.0) * 5

        sector = vp.sector
        sector_bonus = 10.0 if (sector and any(p in sector for p in PRIORITY_SECTORS)) else 0.0

        # Confluence + PEAD
        scanner_match = scanner_by_sym.get(vp.symbol)
        confluence_bonus = 15.0 if (scanner_match and scanner_match.score >= 4) else 0.0
        pead_bonus = _pead_bonus(scanner_match)

        # Stage 2 emphasis (5d/10d alpha)
        stage2_bonus = 8.0 if stage2_ok else 0.0

        composite = (
            v3_score_norm + ce_norm + ol_norm + rsi_norm
            + sector_bonus + confluence_bonus + pead_bonus + stage2_bonus
        )

        meta = {
            "tier": 1,
            "selection_path": "v3_priority",
            "v3_score": vp.score,
            "v3_passed": True,
            "scanner_score": scanner_match.score if scanner_match else 0.0,
            "compression": ce.is_compression,
            "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score,
            "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade,
            "rsi_value": round(rsi.rsi_value, 2),
            "rsi_score": rsi.score,
            "stage2_pass": stage2_ok,
            "sector_bonus": sector_bonus,
            "confluence_bonus": confluence_bonus,
            "pead_bonus": pead_bonus,
            "stage2_bonus": stage2_bonus,
            "regime_score": regime.score,
            "regime_mode": regime.mode,
            "regime_boost": regime_boost,
            "version": "v4",
        }
        tier1.append((composite, vp.symbol, sector, meta))

    # 4) Tier 2: scanner-only with STRICT 2+ quality signals
    tier2: list[tuple[float, str, str | None, dict]] = []
    used_symbols = {vp.symbol for vp in v3_picks}
    for sp in scanner_picks:
        if sp.symbol in used_symbols:
            continue
        q = _evaluate_quality(sp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars, stage2_ok = q

        # 2+ quality signals 필수 (v3는 1+였음)
        signals_count = 0
        if ce.is_compression: signals_count += 1
        if ce.is_expansion: signals_count += 1
        if stage2_ok: signals_count += 1
        sector = sp.sector
        sector_priority = bool(sector and any(p in sector for p in PRIORITY_SECTORS))
        if sector_priority: signals_count += 1
        if open_loc.above_pivot: signals_count += 1
        if rsi.grade == "good": signals_count += 1

        if signals_count < 2:
            continue  # 엄격 — 2+ signals 필수

        scanner_norm = (sp.score / 5.0) * 22
        ce_norm = (ce.score / 6.0) * 25 * regime_boost
        if ce.is_compression and ce.is_expansion:
            ce_norm += 8.0
        ol_norm = (open_loc.score / 5.0) * 12
        rsi_norm = (rsi.score / 5.0) * 8
        stage2_bonus = 12.0 if stage2_ok else 0.0
        sector_bonus = 10.0 if sector_priority else 0.0
        pead_bonus = _pead_bonus(sp)

        composite = (
            scanner_norm + ce_norm + ol_norm + rsi_norm
            + sector_bonus + stage2_bonus + pead_bonus
        )

        meta = {
            "tier": 2,
            "selection_path": "scanner_strict_quality_2plus",
            "v3_score": 0,
            "v3_passed": False,
            "scanner_score": sp.score,
            "stage2_pass": stage2_ok,
            "compression": ce.is_compression,
            "expansion": ce.is_expansion,
            "golden_setup": ce.is_compression and ce.is_expansion,
            "compression_score": ce.score,
            "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade,
            "rsi_value": round(rsi.rsi_value, 2),
            "rsi_score": rsi.score,
            "signals_count": signals_count,
            "sector_bonus": sector_bonus,
            "stage2_bonus": stage2_bonus,
            "pead_bonus": pead_bonus,
            "regime_score": regime.score,
            "regime_mode": regime.mode,
            "version": "v4",
        }
        tier2.append((composite, sp.symbol, sector, meta))

    # 5) Tier 1 우선 + Tier 2 보충
    tier1.sort(key=lambda x: x[0], reverse=True)
    tier2.sort(key=lambda x: x[0], reverse=True)
    combined = tier1 + tier2

    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(combined[:top], start=1):
        out.append(
            PickCandidate(
                system_id="integrated",
                rank=i,
                symbol=sym,
                score=round(composite, 2),
                score_meta=meta,
                sector=sector,
                strategy_tag="swing",
            )
        )
    return out


async def run_integrated_v3(
    target_date: date | None = None,
    top: int = 5,
    *,
    session=None,
    scanner_picks_cached: list[PickCandidate] | None = None,
    v3_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """v3 — v3-priority 선정 + confluence bonus + 엄격 quality filter.

    v2 한계 (5d/10d alpha < v3 standalone): scanner의 broad pool이 알파 희석.
    v3 가설:
      1. v3 picks (반도체/모멘텀 narrow concentrate) 우선 선정
      2. scanner 매칭 시 confluence bonus (+15)
      3. scanner-only는 엄격 quality (compression/expansion/Stage2 중 1개+ 필수)
      4. 좁고 깊은 setup만 통과 — v3 standalone 알파 (5d 4.38%, 10d 7.66%) 추격
    """
    from scanner.comparison.v3_historical import run_v3_for_date
    from api.db.session import async_session_factory

    # 1) Regime gate
    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning("Integrated v3: defensive regime — long blocked")
        return []

    # 2) Universe — v3 picks + scanner picks
    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)

    if v3_picks_cached is not None:
        v3_picks = v3_picks_cached
    else:
        owns_session = session is None
        if owns_session:
            session = async_session_factory()
        try:
            v3_picks = await run_v3_for_date(session, target_date or date.today(), top=15)
        finally:
            if owns_session:
                await session.close()

    scanner_by_sym = {sp.symbol: sp for sp in scanner_picks}
    v3_by_sym = {vp.symbol: vp for vp in v3_picks}

    end_iso = (target_date or date.today()).isoformat()
    start_iso = ((target_date or date.today()) - timedelta(days=120)).isoformat()

    def _evaluate_quality(symbol: str):
        """Returns (ce, rsi, open_loc, last_bar) or None if filter fails."""
        try:
            full_bars = get_bars(symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50:
                return None
            ce = detect_compression_expansion(bars)
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad":
                return None
            last = bars.iloc[-1]
            prev = bars.iloc[-2]
            open_loc = compute_open_location(
                open_price=float(last["open"]),
                pivot_price=float(prev["high"]),
                prev_high=float(prev["high"]),
                prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk:
                return None
            return ce, rsi, open_loc, bars
        except Exception as exc:
            logger.warning("v3 quality eval failed for %s: %s", symbol, exc)
            return None

    # 3) Tier 1: v3 picks (priority — 좁고 깊은 setup pool)
    tier1: list[tuple[float, str, str | None, dict]] = []
    for vp in v3_picks:
        q = _evaluate_quality(vp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, _bars = q

        # v3 score 직접 사용 (최대 50점, v3는 100점 만점)
        v3_score_norm = (vp.score / 100.0) * 50

        # Quality bonuses
        ce_norm = (ce.score / 6.0) * 20
        ol_norm = (open_loc.score / 5.0) * 10
        rsi_norm = (rsi.score / 5.0) * 5

        # Sector concentration bonus
        sector = vp.sector
        sector_bonus = 0.0
        if sector and any(p in sector for p in PRIORITY_SECTORS):
            sector_bonus = 10.0

        # Confluence bonus — scanner도 통과 시
        scanner_match = scanner_by_sym.get(vp.symbol)
        confluence_bonus = 0.0
        scanner_score = 0.0
        if scanner_match and scanner_match.score >= 4:
            confluence_bonus = 15.0
            scanner_score = scanner_match.score

        composite = (
            v3_score_norm + ce_norm + ol_norm + rsi_norm
            + sector_bonus + confluence_bonus
        )

        meta = {
            "tier": 1,
            "selection_path": "v3_priority",
            "v3_score": vp.score,
            "v3_passed": True,
            "scanner_score": scanner_score,
            "scanner_match": scanner_match is not None,
            "compression": ce.is_compression,
            "expansion": ce.is_expansion,
            "compression_score": ce.score,
            "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade,
            "rsi_value": round(rsi.rsi_value, 2),
            "sector_bonus": sector_bonus,
            "confluence_bonus": confluence_bonus,
            "regime_score": regime.score,
            "version": "v3",
        }
        tier1.append((composite, vp.symbol, sector, meta))

    # 4) Tier 2: scanner-only (v3 미통과)에 엄격 quality 필터
    tier2: list[tuple[float, str, str | None, dict]] = []
    used_symbols = {vp.symbol for vp in v3_picks}
    for sp in scanner_picks:
        if sp.symbol in used_symbols:
            continue
        q = _evaluate_quality(sp.symbol)
        if q is None:
            continue
        ce, rsi, open_loc, bars = q

        # 엄격 필터: compression OR expansion OR Stage2 통과 중 1개+ 필수
        from signals.stage2_trend_template import trend_template_pass
        try:
            stage2_ok = bool(trend_template_pass(bars).iloc[-1]) if len(bars) >= 252 else False
        except Exception:
            stage2_ok = False

        has_quality = ce.is_compression or ce.is_expansion or stage2_ok
        if not has_quality:
            continue  # 엄격 필터 — quality 신호 없으면 제외

        # Composite (scanner 비중 ↓, quality ↑)
        scanner_norm = (sp.score / 5.0) * 25
        ce_norm = (ce.score / 6.0) * 30
        ol_norm = (open_loc.score / 5.0) * 15
        rsi_norm = (rsi.score / 5.0) * 10
        stage2_bonus = 10.0 if stage2_ok else 0.0

        sector = sp.sector
        sector_bonus = 0.0
        if sector and any(p in sector for p in PRIORITY_SECTORS):
            sector_bonus = 10.0

        composite = (
            scanner_norm + ce_norm + ol_norm + rsi_norm
            + sector_bonus + stage2_bonus
        )

        meta = {
            "tier": 2,
            "selection_path": "scanner_strict_quality",
            "v3_score": 0,
            "v3_passed": False,
            "scanner_score": sp.score,
            "scanner_match": True,
            "stage2_pass": stage2_ok,
            "compression": ce.is_compression,
            "expansion": ce.is_expansion,
            "compression_score": ce.score,
            "open_location_score": open_loc.score,
            "rsi_grade": rsi.grade,
            "rsi_value": round(rsi.rsi_value, 2),
            "sector_bonus": sector_bonus,
            "stage2_bonus": stage2_bonus,
            "regime_score": regime.score,
            "version": "v3",
        }
        tier2.append((composite, sp.symbol, sector, meta))

    # 5) 통합 — tier1 우선 + tier2 보충
    tier1.sort(key=lambda x: x[0], reverse=True)
    tier2.sort(key=lambda x: x[0], reverse=True)
    combined = tier1 + tier2

    out: list[PickCandidate] = []
    for i, (composite, sym, sector, meta) in enumerate(combined[:top], start=1):
        out.append(
            PickCandidate(
                system_id="integrated",
                rank=i,
                symbol=sym,
                score=round(composite, 2),
                score_meta=meta,
                sector=sector,
                strategy_tag="swing",
            )
        )
    return out


async def run_integrated(
    target_date: date | None = None,
    top: int = 5,
    *,
    scanner_picks_cached: list[PickCandidate] | None = None,
) -> list[PickCandidate]:
    """통합 picks 산출. scanner candidates에 v3 quality layer 적용 후 composite 정렬.

    - scanner 후보 30개 → quality 검증 → top 5 composite
    - bad RSI(climax/divergence) + gap-and-fail은 필터 제외
    - `scanner_picks_cached` 제공 시 fetch_scanner_picks 호출 생략 (백필 최적화)
    """
    # 1) Regime gate
    regime = evaluate_regime(target_date)
    if regime.long_blocked():
        logger.warning(
            "Integrated: defensive regime (score=%.1f) — long blocked",
            regime.score,
        )
        return []

    # 2) Scanner base candidates (캐시 우선)
    if scanner_picks_cached is not None:
        scanner_picks = scanner_picks_cached
    else:
        scanner_picks = await fetch_scanner_picks(target_date, top=30)
    if not scanner_picks:
        logger.info("Integrated: no scanner candidates for %s", target_date)
        return []

    # 3) v3 quality layer 적용
    enriched: list[tuple[float, PickCandidate, dict]] = []
    end_iso = (target_date or date.today()).isoformat()
    start_iso = (
        (target_date or date.today()) - timedelta(days=120)
    ).isoformat()

    for sp in scanner_picks:
        try:
            full_bars = get_bars(sp.symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(full_bars, target_date) if target_date else full_bars
            if bars is None or len(bars) < 50:
                continue

            # C5 Compression / Expansion (0~6)
            ce = detect_compression_expansion(bars)

            # D1 RSI Structure (0~5) — bad grade는 필터 제외
            rsi = detect_rsi_structure(bars)
            if rsi.grade == "bad":
                logger.debug("Integrated filter: %s RSI bad (%s)", sp.symbol, rsi.notes)
                continue

            # C4 Open Location (0~5) — 진입가는 마지막 봉 시초가 사용
            last = bars.iloc[-1]
            prev = bars.iloc[-2] if len(bars) >= 2 else last
            open_loc = compute_open_location(
                open_price=float(last["open"]),
                pivot_price=float(prev["high"]),  # 전일 고점을 피벗으로
                prev_high=float(prev["high"]),
                prev_low=float(prev["low"]),
            )
            if open_loc.gap_and_fail_risk:
                logger.debug("Integrated filter: %s gap-and-fail risk", sp.symbol)
                continue

            # Composite score (0~100)
            composite = (
                (sp.score / 5.0) * W_SCANNER
                + (ce.score / 6.0) * W_COMPRESSION
                + (open_loc.score / 5.0) * W_OPEN_LOC
                + (rsi.score / 5.0) * W_RSI
            )

            quality_meta = {
                "scanner_score": sp.score,
                "scanner_signals": sp.score_meta.get("signals"),
                "scanner_earnings_phase": sp.score_meta.get("earnings_phase"),
                "compression": ce.is_compression,
                "expansion": ce.is_expansion,
                "compression_score": ce.score,
                "compression_ratio": round(ce.compression_ratio, 3),
                "expansion_ratio": round(ce.expansion_ratio, 3),
                "open_location_score": open_loc.score,
                "open_location_above_pivot": open_loc.above_pivot,
                "open_location_above_prev_high": open_loc.above_prev_high,
                "rsi_grade": rsi.grade,
                "rsi_value": round(rsi.rsi_value, 2),
                "rsi_score": rsi.score,
                "regime_score": regime.score,
                "regime_mode": regime.mode,
            }
            enriched.append((composite, sp, quality_meta))
        except Exception as exc:
            logger.warning("Integrated layer failed for %s: %s", sp.symbol, exc)

    # 4) Composite 정렬, top N
    enriched.sort(key=lambda x: x[0], reverse=True)
    out: list[PickCandidate] = []
    for i, (composite, sp, meta) in enumerate(enriched[:top], start=1):
        out.append(
            PickCandidate(
                system_id="integrated",
                rank=i,
                symbol=sp.symbol,
                score=round(composite, 2),
                score_meta=meta,
                sector=sp.sector,
                strategy_tag="swing",
            )
        )
    return out


# ─────────── Swing open-market mode (2026-06-05 도입, ORB 폐지 후속) ───────────

SWING_ATR_PCT_CAP = 5.0  # ATR_pct > 5% 종목 제외 (ARM 등 고변동성 cap)
SWING_ATR_MULT_STOP = 2.0  # entry - 20일 ATR × 2 = stop
SWING_HOLD_DAYS = 5


async def run_swing_picks(
    target_date: date | None = None,
    top: int = 3,
    *,
    session=None,
    atr_pct_cap: float = SWING_ATR_PCT_CAP,
    atr_mult_stop: float = SWING_ATR_MULT_STOP,
    candidate_pool: int = 15,
) -> list[PickCandidate]:
    """Swing open-market watchlist — integrated v10 직접 + ATR cap.

    백테스트 검증 (2026-03-27 ~ 2026-06-03, n=73):
      win 75%, alpha +4.59%, MDD -4.96%, Sharpe(trade) 0.52.

    설계 결정 (2026-06-05):
      - Entry: 09:30 시장가
      - Stop : entry - 20일 ATR × atr_mult_stop
      - Filter: ATR_pct > atr_pct_cap → 제외 (ARM류 고변동성 차단)
      - Exit  : 5영업일 후 종가 강제 (monitor 처리)
      - Top N : preopen 단계 (5-Model Intraday Stack) 우회, v10 알파를 직접 사용

    score_meta:
      atr_pct, atr_mult_stop, provisional_entry/stop/target, hold_days,
      swing_mode=True, version='swing_v1'
    """
    from signals.atr import atr as _atr_series, atr_pct as _atr_pct_series

    today = target_date or date.today()

    v10_picks = await run_integrated_v10(today, top=candidate_pool, session=session)
    if not v10_picks:
        logger.info("[swing] v10 returned no candidates for %s", today)
        return []

    end_iso = today.isoformat()
    start_iso = (today - timedelta(days=60)).isoformat()

    out: list[PickCandidate] = []
    skipped: list[dict] = []

    for vp in v10_picks:
        try:
            bars_full = get_bars(vp.symbol, start_iso, end_iso, "1d")
            bars = _slice_to_date(bars_full, today)
            if bars is None or len(bars) < 22:
                skipped.append({"symbol": vp.symbol, "reason": "insufficient_bars"})
                continue

            atr_v = float(_atr_series(bars, period=20).iloc[-1])
            entry_ref = float(bars["close"].iloc[-1])  # 어제 종가 — 09:30 trade phase가 실제 open으로 재계산
            atr_pct_v = (atr_v / entry_ref) * 100.0 if entry_ref > 0 else 0.0

            if atr_pct_v > atr_pct_cap:
                skipped.append({
                    "symbol": vp.symbol,
                    "reason": f"atr_pct {atr_pct_v:.2f}% > cap {atr_pct_cap}%",
                })
                continue

            stop_dist = atr_v * atr_mult_stop
            provisional_stop = entry_ref - stop_dist
            if provisional_stop <= 0:
                skipped.append({"symbol": vp.symbol, "reason": "invalid_stop"})
                continue
            # take_profit은 5d 강제 exit이라 사실상 무효 — bracket 요건 충족용 (entry × 1.5)
            provisional_target = entry_ref * 1.5

            v10_meta = dict(vp.score_meta or {})
            meta = {
                "swing_mode": True,
                "selection_path": "swing_v10_direct",
                "v10_score": vp.score,
                "v10_meta": v10_meta,
                "atr": round(atr_v, 4),
                "atr_pct": round(atr_pct_v, 3),
                "atr_mult_stop": atr_mult_stop,
                "provisional_entry": round(entry_ref, 4),
                "provisional_stop": round(provisional_stop, 4),
                "provisional_target": round(provisional_target, 4),
                "hold_days": SWING_HOLD_DAYS,
                "regime_score": v10_meta.get("regime_score"),
                "regime_mode": v10_meta.get("regime_mode"),
                "version": "swing_v1",
            }
            out.append(PickCandidate(
                system_id="swing",
                rank=len(out) + 1,
                symbol=vp.symbol,
                score=vp.score,
                score_meta=meta,
                sector=vp.sector,
                strategy_tag="swing",
            ))
            if len(out) >= top:
                break
        except Exception as exc:
            logger.warning("[swing] eval failed for %s: %s", vp.symbol, exc)
            skipped.append({"symbol": vp.symbol, "reason": f"eval_error: {exc}"})

    if skipped:
        logger.info("[swing] skipped (%d): %s", len(skipped), skipped[:5])

    return out
