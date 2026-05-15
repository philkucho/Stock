"""주간 self-critique — provider 무관 (ADVISOR_PROVIDER로 google/anthropic 자동 선택).

지난주 자문 결과(metrics + samples)를 LLM에 보여주고 prompt 개선 제안을 받는다.
출력: prompts/v2/critique_<date>.md (사람이 PR 형태로 검토 후 머지).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from services.advisor.evaluator import compute_weekly, fetch_recent_samples_for_critique
from services.advisor.llm import PROMPT_DIR, get_advisor_client

logger = logging.getLogger("advisor.self_critique")


async def run_weekly_critique(
    session: AsyncSession,
    *,
    target: date | None = None,
    write_file: bool = True,
) -> dict[str, Any]:
    """지난 7일 자문 결과 → LLM에 보내 개선 제안 받기.

    write_file=True면 prompts/v2/critique_<date>.md 생성.
    """
    end = target or date.today()

    metrics = await compute_weekly(session, week_end=end)
    samples = await fetch_recent_samples_for_critique(session, days=7, limit=10)

    if not samples:
        return {"status": "no_samples", "metrics": metrics.to_dict()}

    payload = {
        "period_end": end.isoformat(),
        "metrics": metrics.to_dict(),
        "samples": samples,
    }

    client = get_advisor_client()
    text, usage = await client.call(
        prompt_name="self_critique",
        user_payload=payload,
        max_tokens=3000,
        temperature=0.4,
    )

    result: dict[str, Any] = {
        "status": "ok",
        "period_end": end.isoformat(),
        "metrics": metrics.to_dict(),
        "samples_count": len(samples),
        "critique_text": text,
        "usage": usage,
    }

    if write_file:
        v2_dir = PROMPT_DIR / "v2"
        v2_dir.mkdir(parents=True, exist_ok=True)
        out_path = v2_dir / f"critique_{end.isoformat()}.md"
        out_path.write_text(
            f"# Weekly self-critique — {end.isoformat()}\n\n"
            f"## Provider: {usage.get('provider')} / {usage.get('model')}\n\n"
            f"## Metrics\n\n```json\n{json.dumps(metrics.to_dict(), indent=2)}\n```\n\n"
            f"## Critique\n\n{text}\n",
            encoding="utf-8",
        )
        result["written_to"] = str(out_path)
        logger.info("[self_critique] written %s", out_path)

    return result
