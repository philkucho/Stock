"""거래량 트렌드 시그널 (3-tier funnel).

다중 timeframe 거래량 비율을 점수화:
- 1주 평균 / 1달 평균
- 전일 / 1달 평균

두 비율 모두 임계치 이상이면 +1 ("smart money funnel" 형성).
한쪽만 충족되면 0. 둘 다 미충족이면 0. (음의 케이스는 별도 의미 없어 NEUTRAL.)

당일 1H 거래량은 일봉 데이터에 없어 Phase 2(intraday) 추가 시 별도 시그널로 분리 예정.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

MONTH = 20  # 영업일 기준 1달
WEEK = 5    # 영업일 기준 1주
WEEK_RATIO_THRESHOLD = 1.2   # 1주 평균이 1달 평균의 1.2배 이상
DAY_RATIO_THRESHOLD = 1.5    # 전일 거래량이 1달 평균의 1.5배 이상


@register_signal(
    name="volume_trend",
    description="거래량 깔때기: 1주/1달 ≥ 1.2x AND 전일/1달 ≥ 1.5x",
    category="volume",
    min_bars=MONTH,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    vol = bars["volume"]
    month_avg = vol.rolling(MONTH).mean()
    week_avg = vol.rolling(WEEK).mean()

    week_ratio = week_avg / month_avg
    day_ratio = vol / month_avg

    funnel = (week_ratio >= WEEK_RATIO_THRESHOLD) & (day_ratio >= DAY_RATIO_THRESHOLD)
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[funnel] = 1
    return sig
