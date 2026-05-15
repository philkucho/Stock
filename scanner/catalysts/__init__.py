"""카탈리스트 소스 — ER / 뉴스 / FDA·M&A / 레이팅 변화 분리 (개선안 ⑧).

각 소스는 `(symbol, target_date) -> CatalystHit | None` 형태로 통일.
aggregate_catalyst() 가 모든 소스를 합쳐 단일 CatalystScore (0~5점) + 요약 반환.
"""

from scanner.catalysts.aggregator import aggregate_catalyst
from scanner.catalysts.types import (
    KIND_SCORE,
    CatalystHit,
    CatalystKind,
    CatalystScore,
)

__all__ = [
    "CatalystHit",
    "CatalystKind",
    "CatalystScore",
    "KIND_SCORE",
    "aggregate_catalyst",
]
