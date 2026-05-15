"""AI 투자 자문 에이전트 (Claude Opus 4.7).

- context_builder : picks + regime + positions + news → 프롬프트 컨텍스트
- claude_client   : Anthropic SDK wrapper (prompt caching)
- recommendation  : Pydantic 모델 + 검증
- service         : 전체 자문 흐름 (build context → call → parse → persist → notify)

설계 원칙: AI는 분석가, 사용자가 의사결정자, 시스템이 실행자.
AI는 절대 직접 발주하지 않으며 trade_plan upsert는 사용자 승인 후에만 일어난다.
"""

from services.advisor.recommendation import (
    Recommendation,
    RecommendationAction,
    RecommendationValidationError,
)

__all__ = [
    "Recommendation",
    "RecommendationAction",
    "RecommendationValidationError",
]
