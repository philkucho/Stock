"""VWAP Reclaim — 인트라데이 전환 트리거.

종가가 직전 봉에서는 VWAP 아래였는데 현재 봉에서는 VWAP 위로 마감.
일봉에는 의미 없음 (VWAP는 인트라데이 reset). 5분/1분봉 데이터에서만 유효.

스캐너는 가산 점수(S4 Score)에서 사용. 백테스트에서도 인트라데이 봉에 한해 평가 가능.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

MIN_BARS_FOR_VWAP = 20  # 의미있는 vwap 산출을 위한 최소 봉 수


def session_vwap(bars: pd.DataFrame) -> pd.Series:
    """세션 누적 VWAP. bars의 인덱스는 단일 거래일 인트라데이여야 의미 있음.

    여러 거래일 mixed 입력 시에도 NaN 안 나도록 cumulative 계산.
    실전에선 호출자가 거래일별로 group_by 해서 호출 권장.
    """
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    cum_vol = bars["volume"].cumsum()
    cum_pv = (typical * bars["volume"]).cumsum()
    return cum_pv / cum_vol.replace(0, pd.NA)


@register_signal(
    name="vwap_reclaim",
    description="직전 봉 close가 VWAP 아래였는데 현재 봉 close가 VWAP 위 — 인트라데이 전환 트리거",
    category="reversal",
    min_bars=MIN_BARS_FOR_VWAP,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    vwap = session_vwap(bars)
    above = bars["close"] > vwap
    prev_below = above.shift(1) == False  # noqa: E712 (NaN 처리)
    sig = pd.Series(0, index=bars.index, dtype=int)
    reclaim = above & prev_below
    sig[reclaim.fillna(False)] = 1
    return sig
