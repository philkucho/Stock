"""Backwards-compat shim — provider 추상화 후 import 경로 호환만 유지.

신규 코드는 `services.advisor.llm.get_advisor_client()`를 사용할 것.
"""
from services.advisor.llm import DEFAULT_PROMPT_VERSION, PROMPT_DIR, get_advisor_client
from services.advisor.llm.anthropic import AnthropicAdvisorClient as ClaudeAdvisorClient

__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "PROMPT_DIR",
    "ClaudeAdvisorClient",
    "get_advisor_client",
]
