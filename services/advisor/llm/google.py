"""Google Gemini advisor client.

SDK: google-genai (새 통합 SDK, 2024+).
설치: pip install google-genai

Gemini API 형식 차이:
  - system_instruction은 단일 문자열 (3 블록 join)
  - response_mime_type='application/json'으로 JSON 강제 가능 (파싱 안정성 향상)
  - 무료 티어: Gemini 2.5 Pro 5 RPM / 50 RPD, 2.5 Flash 10 RPM / 250 RPD
  - 429 (rate limit) 시 tenacity 없이 한 번만 wait 후 raise — service.py가 status='claude_error'로 처리

⚠️ 무료 티어 데이터 정책: 입력/출력이 Google 모델 학습에 사용될 수 있음.
  paper 단계에선 허용. live 전환 시 유료 Tier 1로 업그레이드 권장.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from services.advisor.llm.base import LLMAdvisorClient

logger = logging.getLogger("advisor.llm.google")


class GoogleAdvisorClient(LLMAdvisorClient):
    provider_name = "google"

    def _default_model(self) -> str:
        return os.environ.get("ADVISOR_MODEL", "gemini-2.5-pro")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = self.api_key or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise RuntimeError("GOOGLE_API_KEY missing — set in .env")

    def _system_instruction(self, prompt_name: str) -> str:
        """3개 시스템 블록을 단일 텍스트로 join (Gemini 형식)."""
        return "\n\n---\n\n".join(self._system_block_texts(prompt_name))

    async def call(
        self,
        *,
        prompt_name: str,
        user_payload: dict[str, Any] | str,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> tuple[str, dict[str, Any]]:
        # google-genai의 Client는 동기. asyncio.to_thread로 감싸 이벤트 루프 비차단.
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=self.api_key)
        user_text = self._to_user_text(user_payload)
        sys_text = self._system_instruction(prompt_name)

        config = genai_types.GenerateContentConfig(
            system_instruction=sys_text,
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        )

        def _generate() -> Any:
            return client.models.generate_content(
                model=self.model,
                contents=user_text,
                config=config,
            )

        resp = await asyncio.to_thread(_generate)

        text = (resp.text or "").strip()

        usage_meta = getattr(resp, "usage_metadata", None)
        in_toks = getattr(usage_meta, "prompt_token_count", 0) or 0 if usage_meta else 0
        out_toks = getattr(usage_meta, "candidates_token_count", 0) or 0 if usage_meta else 0
        cached_toks = (
            getattr(usage_meta, "cached_content_token_count", 0) or 0
            if usage_meta else 0
        )

        usage = {
            "provider": self.provider_name,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "input_tokens": in_toks,
            "output_tokens": out_toks,
            "cache_read_input_tokens": cached_toks,
            "cache_creation_input_tokens": 0,
            "stop_reason": _finish_reason(resp),
            "cache_hit_pct": (
                round(cached_toks / max(1, in_toks) * 100, 1) if cached_toks else 0.0
            ),
        }
        logger.info(
            "[google] %s in=%d cached=%d out=%d",
            prompt_name, in_toks, cached_toks, out_toks,
        )
        return text, usage


def _finish_reason(resp: Any) -> str | None:
    try:
        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            fr = getattr(candidates[0], "finish_reason", None)
            return str(fr) if fr else None
    except Exception:
        return None
    return None
