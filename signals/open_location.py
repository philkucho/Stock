"""Open Location — C4 Setup score (v3 신규).

같은 +5% 갭이라도 시초가가 어디에 위치했는지가 진입 후 행동을 갈음:
  - 전일 range 상단 위 → strength continuation
  - 52w high 직전 → squeeze 잠재
  - 저항 직밑 → 실패 위험
  - extended 영역 → exhaustion

산출: open_location_score (0~5점) + risk flag (gap-and-fail).

산식:
  +2: 시초가 > 전일 high (range 상단 돌파)
  +2: 시초가 > 피벗 (피벗 위 시초)
  +1: 시초가 > 직전 봉 close (gap-up)이면서 close > VWAP
  -2: gap-and-fail 위험 (시초가 < 전일 low) → P6 페널티로 옮김
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class OpenLocationResult:
    score: float = 0.0           # 0~5
    above_prev_high: bool = False
    above_pivot: bool = False
    vwap_reclaim: bool = False
    gap_and_fail_risk: bool = False  # P6 페널티 트리거


def compute_open_location(
    open_price: float | None,
    pivot_price: float | None,
    prev_high: float | None,
    prev_low: float | None,
    vwap: float | None = None,
    last_close: float | None = None,
) -> OpenLocationResult:
    """단일 시점 평가.

    open_price: 오늘 시가 (또는 진입 추정 가격)
    prev_high/prev_low: 전일 고점/저점
    pivot_price: 피벗 가격 (피벗 = max(전일고점, PMH))
    vwap, last_close: VWAP reclaim 판정용 (선택)
    """
    r = OpenLocationResult()
    if open_price is None or open_price <= 0:
        return r

    if prev_high is not None and open_price > prev_high:
        r.above_prev_high = True
        r.score += 2.0

    if pivot_price is not None and open_price > pivot_price:
        r.above_pivot = True
        r.score += 2.0

    # VWAP reclaim: 마지막 close가 VWAP 위, 시가는 VWAP 아래였거나 비슷
    if vwap is not None and last_close is not None and last_close > vwap and open_price <= vwap * 1.005:
        r.vwap_reclaim = True
        r.score += 1.0

    # gap-and-fail 위험: 시초가가 전일 저점 아래 (강한 약세 신호)
    if prev_low is not None and open_price < prev_low:
        r.gap_and_fail_risk = True

    # 0~5 클램프
    r.score = max(0.0, min(5.0, r.score))
    return r
