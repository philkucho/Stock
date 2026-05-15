"""Relative Strength — IBD 표준 4-quarter weighted + 단순 multi-timeframe RS.

IBD 산식 (William O'Neil & Co):
    RS_raw = 2 × (P / P_{63d}) + (P / P_{126d}) + (P / P_{189d}) + (P / P_{252d})
    RS_Rating = percentile_rank(RS_raw, all_stocks)  # 1~99

Multi-timeframe RS vs SPY (1m/3m/6m): 각 기간 outperformance 부호 → 점수.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

LOOKBACKS = (63, 126, 189, 252)  # 분기 단위
WEIGHTS = (2.0, 1.0, 1.0, 1.0)


def rs_ibd_raw(bars: pd.DataFrame) -> pd.Series:
    """IBD 4-quarter weighted RS raw 값 (percentile 변환 전).

    NaN 처리: 252봉 미만이면 NaN. 호출자가 percentile rank 시 자동 제외.
    """
    closes = bars["close"]
    parts = []
    for lb, w in zip(LOOKBACKS, WEIGHTS):
        ratio = closes / closes.shift(lb)
        parts.append(w * ratio)
    return sum(parts) / sum(WEIGHTS)


def rs_vs_benchmark(bars: pd.DataFrame, benchmark: pd.DataFrame, lookback: int) -> float | None:
    """단일 시점 RS = (Stock_t / Stock_{t-lb}) ÷ (Bench_t / Bench_{t-lb}).

    1이면 동률, >1 outperform, <1 underperform.
    """
    if len(bars) <= lookback or len(benchmark) <= lookback:
        return None
    s_now = float(bars["close"].iloc[-1])
    s_then = float(bars["close"].iloc[-(lookback + 1)])
    b_now = float(benchmark["close"].iloc[-1])
    b_then = float(benchmark["close"].iloc[-(lookback + 1)])
    if s_then <= 0 or b_then <= 0:
        return None
    return (s_now / s_then) / (b_now / b_then)


def rs_percentile(stock_raw: float, peer_raws: list[float]) -> int:
    """1~99 percentile rank. peer_raws에 stock_raw 포함되어도 OK."""
    if not peer_raws or stock_raw is None:
        return 0
    sorted_raws = sorted([r for r in peer_raws if r is not None and not pd.isna(r)])
    n = len(sorted_raws)
    if n == 0:
        return 0
    below = sum(1 for r in sorted_raws if r < stock_raw)
    pct = round((below / n) * 99) + 1
    return max(1, min(99, pct))


@register_signal(
    name="rs_strong",
    description="IBD RS Rating ≥ 70 (universe + SPY/QQQ percentile)",
    category="trend",
    min_bars=max(LOOKBACKS) + 1,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    """단일 종목으로는 percentile 산출 불가 → 호출자가 universe 비교 필요.

    여기서는 raw 값이 양수(가격 상승)인지만 +1로 표시. 실제 점수는 scanner가 산출.
    """
    raw = rs_ibd_raw(bars)
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[raw > 1.0] = 1  # raw>1 = 직전 분기 가격 상승
    return sig
