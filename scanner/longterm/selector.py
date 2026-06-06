"""중장기 선정 엔진 — production 모듈 (백테스트와 1:1 일치 로직).

설계 ([[swing-mode-v1]] 후속, alembic 0014):
  - Universe : S&P 500 (data/sp500_full.json)
  - 게이트   : Stage 2 trend (8조건 AND) + above MA200 + near 52w high(-10%) + $20M ADV
  - 점수(90) : RS IBD percentile(40) + 12mo momentum(25) + 200SMA dist(15) + Stage2 c-margin(10)
  - Top N    : 10
  - Turnover : 60/40 (직전 top 10 중 최대 6 보존, 신규 4 슬롯)
  - Regime   : SPY < 200SMA → defensive (신규 0, holdings 유지)

백테스트 산출물 `backtests/run_longterm.py` 와 함수 이름/로직 동일.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from backtests.data_cache import get_bars
from signals.relative_strength import rs_ibd_raw
from signals.stage2_trend_template import trend_template_diagnostic, trend_template_pass

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)

# 설계 상수 (v2 펀더멘털 도입 — 2026-06-05)
ADV_MIN_DOLLARS = 20_000_000.0
NEAR_52W_HIGH_PCT = 0.10
SMA200_FAR_PCT = 0.50
TURNOVER_CAP_KEEP = 6
TOP_N_DEFAULT = 10
REGIME_SPY_SMA = 200

# v2 점수 가중치 (100점 만점)
RS_PCT_WEIGHT = 25.0            # v1 40 → 25 (의존도 축소)
MOM_12MO_WEIGHT = 15.0          # v1 25 → 15
SMA200_DIST_WEIGHT = 10.0       # v1 15 → 10
STAGE2_MARGIN_WEIGHT = 5.0      # v1 10 → 5
REV_YOY_WEIGHT = 15.0           # NEW: 매출 YoY percentile
EPS_YOY_WEIGHT = 10.0           # NEW: EPS YoY percentile
PEG_WEIGHT = 10.0               # NEW: PEG 낮을수록 +
QOQ_ACCEL_WEIGHT = 5.0          # NEW: 분기 성장 가속화
FCF_MARGIN_WEIGHT = 5.0         # NEW: FCF / Revenue

# v2 펀더 게이트 (누락 시 fail-soft = 통과)
REVENUE_YOY_MIN = 0.05          # +5% YoY 매출 성장 미만 컷
FORWARD_PE_MAX = 40.0           # forward P/E > 40 컷 (성장주 한계)
EPS_YOY_MIN = -0.20             # EPS YoY < -20% (대폭 감익) 컷

# v1 호환 — 백테스트 v1은 import 시 ImportError 방지
SECTOR_PENALTY = 0.0


def _evaluate_symbol(
    symbol: str,
    bars: pd.DataFrame,
    eval_ts: pd.Timestamp,
    fundamentals: dict | None = None,
) -> dict | None:
    """eval_ts 시점까지의 데이터 + 펀더멘털로 게이트 + score component 산출.

    fundamentals: scanner.longterm.fundamentals 의 dict (없으면 펀더 게이트 fail-soft).
    """
    hist = bars[bars.index < eval_ts]
    if len(hist) < 252:
        return None

    last = hist.iloc[-1]
    last_close = float(last["close"])

    stage2 = bool(trend_template_pass(hist).iloc[-1])
    sma200 = float(hist["close"].rolling(200).mean().iloc[-1])
    high_52w = float(hist["high"].rolling(252).max().iloc[-1])
    last_30 = hist.tail(30)
    adv_30d = float((last_30["close"] * last_30["volume"]).mean())

    # 기술적 게이트
    gate_results = {
        "stage2": stage2,
        "above_ma200": last_close > sma200,
        "near_52w_high": last_close >= high_52w * (1.0 - NEAR_52W_HIGH_PCT),
        "adv_ok": adv_30d >= ADV_MIN_DOLLARS,
    }

    # v2 펀더 게이트 (누락 시 fail-soft = True 처리, 단 데이터 있으면 엄격하게 컷)
    fund = fundamentals or {}
    rev_yoy = fund.get("revenue_yoy")
    eps_yoy = fund.get("eps_yoy")
    fwd_pe = fund.get("forward_pe")
    trailing_pe = fund.get("trailing_pe")

    gate_results["revenue_growth"] = (
        rev_yoy is None or rev_yoy >= REVENUE_YOY_MIN
    )
    gate_results["valuation_ok"] = (
        fwd_pe is None or (0 < fwd_pe <= FORWARD_PE_MAX)
    ) and (trailing_pe is None or trailing_pe > 0)  # trailing PE 음수 = 적자
    gate_results["earnings_ok"] = (
        eps_yoy is None or eps_yoy >= EPS_YOY_MIN
    )

    if not all(gate_results.values()):
        return {"symbol": symbol, "gates_passed": False, "gate_results": gate_results}

    rs_raw = float(rs_ibd_raw(hist).iloc[-1])
    close_252 = float(hist["close"].iloc[-252])
    mom_12mo = (last_close / close_252) - 1.0 if close_252 > 0 else 0.0
    sma200_dist = (last_close / sma200) - 1.0

    diag = trend_template_diagnostic(hist)
    sma200_prev = (
        float(hist["close"].rolling(200).mean().iloc[-22])
        if len(hist) >= 222
        else sma200
    )
    c6_margin = (sma200 / sma200_prev) - 1.0 if sma200_prev > 0 else 0.0
    low_52w = float(hist["low"].rolling(252).min().iloc[-1])
    c7_margin = (last_close / (low_52w * 1.30)) - 1.0 if low_52w > 0 else 0.0
    # c8: -25% 기준선 대비 여유 (0 = 정확히 -25%, 1/3 = 신고가). ×3으로 0~1 정규화.
    c8_margin = (last_close / (high_52w * 0.75)) - 1.0 if high_52w > 0 else 0.0
    c_margin = (
        max(0.0, min(1.0, c6_margin / 0.05))
        + max(0.0, min(1.0, c7_margin / 0.50))
        + max(0.0, min(1.0, c8_margin * 3.0))
    ) / 3.0

    return {
        "symbol": symbol,
        "gates_passed": True,
        "gate_results": gate_results,
        "last_close": last_close,
        "rs_raw": rs_raw,
        "mom_12mo": mom_12mo,
        "sma200_dist": sma200_dist,
        "c_margin": c_margin,
        "adv_30d": adv_30d,
        "high_52w": high_52w,
        "stage2_diag": diag,
        # v2 펀더 raw 필드 (점수 계산에서 사용)
        "fund_rev_yoy": rev_yoy,
        "fund_eps_yoy": eps_yoy,
        "fund_peg": fund.get("peg_ratio"),
        "fund_fwd_pe": fwd_pe,
        "fund_fcf_margin": fund.get("fcf_margin"),
        "fund_eps_qoq": fund.get("eps_qoq"),
        "fund_rev_qoq": fund.get("revenue_qoq"),
        "fund_profit_margin": fund.get("profit_margin"),
    }


def _pct_rank(value: float | None, sorted_pool: list[float]) -> int | None:
    """sorted_pool 내 value의 percentile rank (1~99). None → None."""
    if value is None or not sorted_pool:
        return None
    below = sum(1 for r in sorted_pool if r < value)
    return max(1, min(99, round((below / len(sorted_pool)) * 99) + 1))


def _peg_score(peg: float | None) -> float:
    """PEG ratio → 0~1 점수. 낮을수록 좋음. None → 0.5 (중립)."""
    if peg is None or peg <= 0:
        return 0.5
    if peg < 1.0:
        return 1.0
    if peg < 1.5:
        return 0.75
    if peg < 2.0:
        return 0.4
    if peg < 3.0:
        return 0.15
    return 0.0


def _score_candidates(candidates: list[dict]) -> list[dict]:
    """게이트 통과 종목 → composite score 추가, 정렬 (v2: 펀더 포함 100점)."""
    passed = [c for c in candidates if c.get("gates_passed")]
    if not passed:
        return []

    n = len(passed)
    raws = sorted(c["rs_raw"] for c in passed)
    moms = sorted(c["mom_12mo"] for c in passed)
    # 펀더 cross-sectional pools (None 제외)
    rev_pool = sorted(c["fund_rev_yoy"] for c in passed if c.get("fund_rev_yoy") is not None)
    eps_pool = sorted(c["fund_eps_yoy"] for c in passed if c.get("fund_eps_yoy") is not None)
    fcf_pool = sorted(
        c["fund_fcf_margin"] for c in passed if c.get("fund_fcf_margin") is not None
    )

    for c in passed:
        # 기술적 점수
        below = sum(1 for r in raws if r < c["rs_raw"])
        c["rs_pct"] = max(1, min(99, round((below / n) * 99) + 1))

        below_m = sum(1 for m in moms if m < c["mom_12mo"])
        c["mom_pct"] = max(1, min(99, round((below_m / n) * 99) + 1))
        mom_norm = c["mom_pct"] / 99.0

        d = c["sma200_dist"]
        if d <= SMA200_FAR_PCT:
            sma_norm = max(0.0, min(1.0, d / SMA200_FAR_PCT))
        else:
            over = (d - SMA200_FAR_PCT) / SMA200_FAR_PCT
            sma_norm = max(0.0, 1.0 - over)

        # 펀더 점수 — None은 중립 (0.5)
        rev_pct = _pct_rank(c.get("fund_rev_yoy"), rev_pool)
        c["rev_pct"] = rev_pct
        rev_norm = (rev_pct / 99.0) if rev_pct is not None else 0.5

        eps_pct = _pct_rank(c.get("fund_eps_yoy"), eps_pool)
        c["eps_pct"] = eps_pct
        eps_norm = (eps_pct / 99.0) if eps_pct is not None else 0.5

        fcf_pct = _pct_rank(c.get("fund_fcf_margin"), fcf_pool)
        c["fcf_pct"] = fcf_pct
        fcf_norm = (fcf_pct / 99.0) if fcf_pct is not None else 0.5

        peg_norm = _peg_score(c.get("fund_peg"))

        # QoQ 가속화: eps_qoq > eps_yoy (분기 가속화)
        eps_qoq = c.get("fund_eps_qoq")
        eps_yoy = c.get("fund_eps_yoy")
        qoq_norm = 1.0 if (eps_qoq is not None and eps_yoy is not None and eps_qoq > eps_yoy) else 0.0
        if eps_qoq is None or eps_yoy is None:
            qoq_norm = 0.5  # 중립

        composite = (
            (c["rs_pct"] / 99.0) * RS_PCT_WEIGHT
            + mom_norm * MOM_12MO_WEIGHT
            + sma_norm * SMA200_DIST_WEIGHT
            + c["c_margin"] * STAGE2_MARGIN_WEIGHT
            + rev_norm * REV_YOY_WEIGHT
            + eps_norm * EPS_YOY_WEIGHT
            + peg_norm * PEG_WEIGHT
            + qoq_norm * QOQ_ACCEL_WEIGHT
            + fcf_norm * FCF_MARGIN_WEIGHT
        )
        c["composite_score"] = round(composite, 2)
        c["score_breakdown"] = {
            # 기술적
            "rs_pct": c["rs_pct"],
            "mom_pct": c["mom_pct"],
            "mom_12mo": round(c["mom_12mo"], 4),
            "sma200_dist": round(c["sma200_dist"], 4),
            "c_margin": round(c["c_margin"], 3),
            "adv_30d_musd": round(c["adv_30d"] / 1_000_000, 1),
            # 펀더
            "rev_yoy": c.get("fund_rev_yoy"),
            "rev_pct": rev_pct,
            "eps_yoy": c.get("fund_eps_yoy"),
            "eps_pct": eps_pct,
            "forward_pe": c.get("fund_fwd_pe"),
            "peg_ratio": c.get("fund_peg"),
            "peg_norm": round(peg_norm, 2),
            "fcf_margin": c.get("fund_fcf_margin"),
            "fcf_pct": fcf_pct,
            "qoq_accel": qoq_norm > 0.5,
            "profit_margin": c.get("fund_profit_margin"),
            # 가중치 (UI 디버깅용)
            "version": "v2",
            "weights": {
                "rs": RS_PCT_WEIGHT, "mom": MOM_12MO_WEIGHT,
                "sma": SMA200_DIST_WEIGHT, "stage2": STAGE2_MARGIN_WEIGHT,
                "rev": REV_YOY_WEIGHT, "eps": EPS_YOY_WEIGHT,
                "peg": PEG_WEIGHT, "qoq": QOQ_ACCEL_WEIGHT, "fcf": FCF_MARGIN_WEIGHT,
            },
        }

    return sorted(passed, key=lambda x: x["composite_score"], reverse=True)


def _apply_turnover_cap(
    scored: list[dict],
    prev_holdings: list[str],
    top_n: int,
    keep_max: int = TURNOVER_CAP_KEEP,
) -> list[dict]:
    if not prev_holdings:
        return scored[:top_n]
    extended_syms = {c["symbol"] for c in scored[: top_n * 2]}
    kept = [
        c for c in scored
        if c["symbol"] in prev_holdings and c["symbol"] in extended_syms
    ][:keep_max]
    kept_syms = {c["symbol"] for c in kept}
    fresh = [c for c in scored if c["symbol"] not in kept_syms]
    return (kept + fresh)[:top_n]


def _evaluate_regime(spy_bars: pd.DataFrame, eval_ts: pd.Timestamp) -> bool:
    """SPY < 200SMA → True (defensive, 신규 차단)."""
    hist = spy_bars[spy_bars.index < eval_ts]
    if len(hist) < REGIME_SPY_SMA:
        return False
    sma = float(hist["close"].rolling(REGIME_SPY_SMA).mean().iloc[-1])
    last = float(hist["close"].iloc[-1])
    return last < sma


def _load_universe() -> list[str]:
    with open(REPO_ROOT / "data" / "sp500_full.json") as f:
        return json.load(f)["tickers"]


async def run_longterm_selection(
    target_date: date,
    *,
    top_n: int = TOP_N_DEFAULT,
    prev_holdings_symbols: list[str] | None = None,
    universe: list[str] | None = None,
) -> dict:
    """중장기 monthly 선정 — production 엔트리.

    Args:
        target_date: rebalance 기준일 (보통 월 첫 거래일)
        top_n: 선정 종목 수
        prev_holdings_symbols: 직전 월 holdings (60/40 turnover 룰 적용용)
        universe: 평가 universe (None → S&P 500)

    Returns:
        {
          "target_date", "defensive", "candidates_total", "candidates_passed",
          "picks": [{rank, symbol, composite_score, gate_results, score_breakdown,
                     status, fidelity_action, weight_pct}]
        }
    """
    target_ts = pd.Timestamp(target_date, tz="UTC")
    prev_holdings = prev_holdings_symbols or []

    if universe is None:
        universe = _load_universe()
    logger.info("[longterm] universe: %d tickers", len(universe))

    # SPY for regime
    spy = get_bars("SPY", "2017-01-01", target_date.isoformat())
    if spy.empty:
        raise RuntimeError("SPY bars unavailable")
    defensive = _evaluate_regime(spy, target_ts)

    out: dict = {
        "target_date": target_date.isoformat(),
        "defensive": defensive,
        "candidates_total": len(universe),
        "candidates_passed": 0,
        "picks": [],
        "skipped": [],
    }

    if defensive:
        logger.warning("[longterm] DEFENSIVE — SPY < 200SMA. 신규 0, holdings 유지 권고.")
        # 기존 holdings는 cron이 status='hold', fidelity_action='HOLD'로 유지 처리
        out["status"] = "defensive"
        return out

    # v2: 펀더 캐시 일괄 로드 (디스크에서)
    from scanner.longterm.fundamentals import load_cached_universe
    fund_map = load_cached_universe()
    logger.info("[longterm] fundamentals cached: %d symbols", len(fund_map))

    candidates: list[dict] = []
    fetch_start = "2017-01-01"  # 252봉 + warmup
    fetch_end = target_date.isoformat()
    for sym in universe:
        try:
            bars = get_bars(sym, fetch_start, fetch_end)
            if bars is None or bars.empty or len(bars) < 252:
                out["skipped"].append({"symbol": sym, "reason": "insufficient_bars"})
                continue
            fund = fund_map.get(sym.upper())
            ev = _evaluate_symbol(sym, bars, target_ts, fundamentals=fund)
            if ev is not None:
                candidates.append(ev)
        except Exception as exc:
            out["skipped"].append({"symbol": sym, "reason": f"err: {exc}"})

    passed = [c for c in candidates if c.get("gates_passed")]
    out["candidates_passed"] = len(passed)
    logger.info("[longterm] gate-pass: %d / %d", len(passed), len(candidates))

    scored = _score_candidates(candidates)
    final = _apply_turnover_cap(scored, prev_holdings, top_n)

    weight_pct = round(100.0 / max(top_n, 1), 2)
    for i, c in enumerate(final, start=1):
        sym = c["symbol"]
        was_held = sym in prev_holdings
        status = "hold" if was_held else "new"
        fidelity_action = "HOLD" if was_held else "BUY"
        out["picks"].append({
            "rank": i,
            "symbol": sym,
            "sector": None,  # v2에서 추가
            "composite_score": c["composite_score"],
            "gate_results": c["gate_results"],
            "score_breakdown": c["score_breakdown"],
            "status": status,
            "fidelity_action": fidelity_action,
            "weight_pct": weight_pct,
        })

    # 직전 holdings 중 Top 20 밖으로 밀린 종목은 'exited' 권고
    final_syms = {c["symbol"] for c in final}
    top_20_syms = {c["symbol"] for c in scored[: top_n * 2]}
    for prev_sym in prev_holdings:
        if prev_sym in final_syms:
            continue
        action = "TRIM" if prev_sym in top_20_syms else "SELL"
        status = "exit_suggested" if prev_sym in top_20_syms else "exited"
        out["picks"].append({
            "rank": None,
            "symbol": prev_sym,
            "sector": None,
            "composite_score": next(
                (c["composite_score"] for c in scored if c["symbol"] == prev_sym),
                0.0,
            ),
            "gate_results": next(
                (c.get("gate_results", {}) for c in candidates if c["symbol"] == prev_sym),
                {},
            ),
            "score_breakdown": next(
                (c.get("score_breakdown", {}) for c in scored if c["symbol"] == prev_sym),
                {},
            ),
            "status": status,
            "fidelity_action": action,
            "weight_pct": 0.0,
        })

    out["status"] = "ok"
    return out
