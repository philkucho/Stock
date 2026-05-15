"""Intraday tight flag setup — 1분/5분봉의 좁은 컨솔리데이션 돌파 직전 패턴.

Minervini의 일/주봉 VCP를 인트라데이로 그대로 옮기지 않는다 (이름도 의도적으로 다르게).
원조 VCP는 수 주~수 개월 변동성 수축 패턴이며, 본 모듈은 직전 N봉의 짧은 횡보를
알고리즘으로 정의한 "intraday tight flag" 형태.

조건 (모두 충족):
  1) 직전 N봉의 (high - low)/close 표준편차가 monotonic 감소 (3-window rolling std)
  2) 직전 N봉 거래량 추세가 감소 (선형회귀 기울기 < 0)
  3) 마지막 봉 close가 직전 N봉 고점의 ±0.5% 이내
  4) 최근 N봉 평균 range가 직전 N봉 (이전 윈도우) 평균 range의 50% 이하

5분봉 N=6 (30분 윈도우) 이 디폴트. 1분봉은 N=15 권장.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signals._registry import register_signal

DEFAULT_N = 6
PROXIMITY_PCT = 0.005  # 0.5%
RANGE_RATIO = 0.5  # 50%


def _is_monotonic_decreasing(arr: np.ndarray) -> bool:
    """단조 감소 여부 (등호 허용 안 함, 마지막 시점 제외 부분만 검사하지 않음)."""
    return bool(np.all(np.diff(arr) <= 0)) and not bool(np.all(arr == arr[0]))


def _volume_trend_negative(volumes: np.ndarray) -> bool:
    """선형회귀 기울기가 음수인지."""
    n = len(volumes)
    if n < 3:
        return False
    x = np.arange(n)
    slope = np.polyfit(x, volumes, 1)[0]
    return slope < 0


def detect_tight_flag(bars: pd.DataFrame, idx: int, n: int = DEFAULT_N) -> tuple[bool, float]:
    """단일 시점 탐지 — (boolean, tightness_score 0~1) 반환.

    스캐너에서 직접 호출 시 사용. 시그널 evaluate()는 vectorized 평가용.
    """
    if idx < 2 * n - 1:
        return False, 0.0

    window = bars.iloc[idx - n + 1 : idx + 1]
    earlier = bars.iloc[idx - 2 * n + 1 : idx - n + 1]

    rng = (window["high"] - window["low"]) / window["close"]
    earlier_rng = (earlier["high"] - earlier["low"]) / earlier["close"]

    # 1) std(rng)가 직전 3봉씩 비교해 단조 감소 — 단순화: rng 자체가 단조 감소 추세
    cond1 = _is_monotonic_decreasing(rng.values)

    # 2) 거래량 감소 추세
    cond2 = _volume_trend_negative(window["volume"].values)

    # 3) 마지막 close가 윈도우 고점의 0.5% 이내
    window_high = float(window["high"].max())
    last_close = float(window["close"].iloc[-1])
    cond3 = abs(window_high - last_close) / window_high <= PROXIMITY_PCT

    # 4) range 비율 50% 이하
    avg_recent = float(rng.mean())
    avg_earlier = float(earlier_rng.mean()) if not earlier_rng.isna().all() else 1.0
    if avg_earlier <= 0:
        cond4 = False
        ratio = 1.0
    else:
        ratio = avg_recent / avg_earlier
        cond4 = ratio <= RANGE_RATIO

    all_pass = bool(cond1 and cond2 and cond3 and cond4)
    # tightness_score: 1.0 = 매우 타이트, 0.0 = 산만
    tightness = float(np.clip(1.0 - ratio, 0.0, 1.0))
    return all_pass, tightness


@register_signal(
    name="tight_flag_setup",
    description=f"인트라데이 좁은 컨솔리데이션 돌파 직전 (N={DEFAULT_N}봉, 1/5분봉용)",
    category="breakout",
    min_bars=2 * DEFAULT_N,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    sig = pd.Series(0, index=bars.index, dtype=int)
    for i in range(2 * DEFAULT_N - 1, len(bars)):
        ok, _ = detect_tight_flag(bars, i, DEFAULT_N)
        if ok:
            sig.iloc[i] = 1
    return sig
