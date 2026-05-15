"""Anthropic (Claude) advisor client.

System 블록을 3개로 분리하고 각각 cache_control={"type":"ephemeral"} 부착 → cache hit 90%+.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from services.advisor.llm.base import LLMAdvisorClient

logger = logging.getLogger("advisor.llm.anthropic")


class AnthropicAdvisorClient(LLMAdvisorClient):
    provider_name = "anthropic"

    def _default_model(self) -> str:
        return os.environ.get("ADVISOR_MODEL", "claude-opus-4-7")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY missing — set in .env")

    def _build_system_blocks(self, prompt_name: str) -> list[dict[str, Any]]:
        return [
            {"type": "text", "text": txt, "cache_control": {"type": "ephemeral"}}
            for txt in self._system_block_texts(prompt_name)
        ]

    async def call(
        self,
        *,
        prompt_name: str,
        user_payload: dict[str, Any] | str,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> tuple[str, dict[str, Any]]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self.api_key)
        user_text = self._to_user_text(user_payload)
        try:
            msg = await client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=self._build_system_blocks(prompt_name),
                messages=[{"role": "user", "content": user_text}],
            )
        finally:
            try:
                await client.close()
            except Exception:
                pass

        text_parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        text = "\n".join(text_parts)

        cached = getattr(msg.usage, "cache_read_input_tokens", 0) or 0
        cache_created = getattr(msg.usage, "cache_creation_input_tokens", 0) or 0
        in_toks = getattr(msg.usage, "input_tokens", 0) or 0
        out_toks = getattr(msg.usage, "output_tokens", 0) or 0
        total_in = in_toks + cached + cache_created

        usage = {
            "provider": self.provider_name,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "input_tokens": in_toks,
            "output_tokens": out_toks,
            "cache_creation_input_tokens": cache_created,
            "cache_read_input_tokens": cached,
            "stop_reason": getattr(msg, "stop_reason", None),
            "cache_hit_pct": round(cached / total_in * 100, 1) if total_in else 0.0,
        }
        logger.info(
            "[anthropic] %s in=%d cached=%d out=%d",
            prompt_name, in_toks, cached, out_toks,
        )
        return text, usage
