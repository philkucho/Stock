"""LLM provider 추상화.

환경변수 ADVISOR_PROVIDER로 백엔드 선택:
  - 'google'    : Gemini 2.5 Pro (무료 티어, 기본)
  - 'anthropic' : Claude Opus 4.7 / Sonnet 4.6 (유료)

provider별로 prompt caching·system instruction 형식이 다르지만
LLMAdvisorClient.call() 인터페이스는 동일 — 상위 service.py는 provider를 모름.
"""
from __future__ import annotations

import os

from services.advisor.llm.base import (
    DEFAULT_PROMPT_VERSION,
    PROMPT_DIR,
    LLMAdvisorClient,
)


def get_advisor_client(provider: str | None = None) -> LLMAdvisorClient:
    """ADVISOR_PROVIDER 기반 client factory.

    명시 provider arg가 우선. 미지정 시 env 변수, 그것도 없으면 google (무료) 기본.
    """
    name = (provider or os.environ.get("ADVISOR_PROVIDER", "google")).strip().lower()
    if name == "google" or name == "gemini":
        from services.advisor.llm.google import GoogleAdvisorClient
        return GoogleAdvisorClient()
    if name == "anthropic" or name == "claude":
        from services.advisor.llm.anthropic import AnthropicAdvisorClient
        return AnthropicAdvisorClient()
    raise ValueError(
        f"Unknown ADVISOR_PROVIDER: {name!r} (google | anthropic)"
    )


__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "LLMAdvisorClient",
    "PROMPT_DIR",
    "get_advisor_client",
]
