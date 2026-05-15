"""Mark Minervini Stage 2 Trend Template — 8-criteria 동시 충족.

원조: *Trade Like a Stock Market Wizard* (2013).

조건 (모두 AND):
  1) P > 50 SMA
  2) P > 150 SMA
  3) P > 200 SMA
  4) 50 SMA > 150 SMA
  5) 150 SMA > 200 SMA
  6) 200 SMA가 직전 1개월(약 21봉) 동안 상승 (또는 보합)
  7) P가 52주 저점 대비 +30% 이상
  8) P가 52주 고점 대비 -25% 이내

추가 (선택): RS Rating ≥ 70 (별도 `rs_strong` 시그널과 결합).
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

SMA_50 = 50
SMA_150 = 150
SMA_200 = 200
LOOKBACK_52W = 252
TREND_LOOKBACK = 21  # 200 SMA 추세 확인 기간
MIN_FROM_LOW = 0.30  # +30%
MAX_FROM_HIGH = 0.25  # -25% 이내


def trend_template_pass(bars: pd.DataFrame) -> pd.Series:
    """각 인덱스에서 8조건 모두 충족 시 1, 아니면 0."""
    close = bars["close"]
    sma50 = close.rolling(SMA_50).mean()
    sma150 = close.rolling(SMA_150).mean()
    sma200 = close.rolling(SMA_200).mean()
    sma200_prev = sma200.shift(TREND_LOOKBACK)

    high_52w = bars["high"].rolling(LOOKBACK_52W).max()
    low_52w = bars["low"].rolling(LOOKBACK_52W).min()

    cond1 = close > sma50
    cond2 = close > sma150
    cond3 = close > sma200
    cond4 = sma50 > sma150
    cond5 = sma150 > sma200
    cond6 = sma200 >= sma200_prev  # 보합 OK (엄격하면 >, 미네르비니는 추세)
    cond7 = close >= low_52w * (1 + MIN_FROM_LOW)
    cond8 = close >= high_52w * (1 - MAX_FROM_HIGH)

    all_pass = cond1 & cond2 & cond3 & cond4 & cond5 & cond6 & cond7 & cond8
    return all_pass.fillna(False).astype(int)


def trend_template_diagnostic(bars: pd.DataFrame) -> dict[str, bool]:
    """단일 시점 (마지막 봉) 진단 — 8조건 각각 통과 여부 dict."""
    if len(bars) < LOOKBACK_52W:
        return {f"c{i}": False for i in range(1, 9)}
    close = bars["close"]
    last = float(close.iloc[-1])
    sma50_last = float(close.rolling(SMA_50).mean().iloc[-1])
    sma150_last = float(close.rolling(SMA_150).mean().iloc[-1])
    sma200_last = float(close.rolling(SMA_200).mean().iloc[-1])
    sma200_prev_idx = -1 - TREND_LOOKBACK
    sma200_prev = float(close.rolling(SMA_200).mean().iloc[sma200_prev_idx]) if abs(sma200_prev_idx) <= len(close) else 0
    high_52w = float(bars["high"].rolling(LOOKBACK_52W).max().iloc[-1])
    low_52w = float(bars["low"].rolling(LOOKBACK_52W).min().iloc[-1])
    return {
        "c1_above_50sma": last > sma50_last,
        "c2_above_150sma": last > sma150_last,
        "c3_above_200sma": last > sma200_last,
        "c4_50_above_150": sma50_last > sma150_last,
        "c5_150_above_200": sma150_last > sma200_last,
        "c6_200_uptrend": sma200_last >= sma200_prev,
        "c7_above_30pct_52w_low": last >= low_52w * (1 + MIN_FROM_LOW) if low_52w > 0 else False,
        "c8_within_25pct_52w_high": last >= high_52w * (1 - MAX_FROM_HIGH) if high_52w > 0 else False,
    }


@register_signal(
    name="stage2_trend_template",
    description="Minervini Stage 2 Trend Template (8-criteria 동시 충족)",
    category="trend",
    min_bars=LOOKBACK_52W,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    return trend_template_pass(bars)
