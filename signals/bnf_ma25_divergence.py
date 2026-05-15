"""25일 이동평균선 이격률 시그널 — BNF 핵심 매수 룰.

BNF(고테가와 타카시)의 일관된 진입 시그널: 25일 SMA 대비 종가가 깊게 마이너스 이격되면
평균회귀 매수. 일본 원본은 -20~-35%, 미국 대형주에 맞춰 -10%로 완화.

이격률 = (close - SMA25) / SMA25
- divergence <= -0.10  → +1 (매수: 25일 평균보다 10% 이상 아래)
- divergence >= +0.15  → -1 (매도: 과열, BNF의 익절 가이드 영역)

`bb_lower_bounce` (BB ±2σ 복귀)와 차이:
- BB는 분포 정규화된 *spike* — 한 봉 짜리 +1
- divergence는 절대 % 이격 *zone* — 깊은 dip이 며칠 지속되면 +1 유지
- composite에서 진입 후 보유 중에도 신호 +1 유지 가능 → 신호 락인 방지를 위해
  `CompositeStrategy.max_hold_bars` 시간 청산과 함께 사용 권장.
"""

from __future__ import annotations

import pandas as pd

from signals._registry import register_signal

PERIOD = 25
LOWER_THRESHOLD = -0.07  # 25일 MA 대비 -7% 이하 → 매수 (미국 대형주 변동성 보정, BNF 일본 -20~-35% → 미국 -7~-10%)
UPPER_THRESHOLD = +0.12  # +12% 이상 → 매도 (익절 가이드)


@register_signal(
    name="bnf_ma25_divergence",
    description="종가의 25일 SMA 이격률 ≤ -10% 매수 / ≥ +15% 매도 (BNF 핵심 룰)",
    category="reversal",
    min_bars=PERIOD,
)
def evaluate(bars: pd.DataFrame) -> pd.Series:
    close = bars["close"]
    ma = close.rolling(PERIOD).mean()
    divergence = (close - ma) / ma
    sig = pd.Series(0, index=bars.index, dtype=int)
    sig[divergence <= LOWER_THRESHOLD] = 1
    sig[divergence >= UPPER_THRESHOLD] = -1
    return sig
