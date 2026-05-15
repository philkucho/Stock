"""LLM advisor client 공통 base.

prompt 로딩, 3-tier system text 구성, _read_or_default fallback은 provider 공통.
provider별 차이:
  - Anthropic : system을 dict list로 보내고 각 블록에 cache_control 부착
  - Google    : system_instruction에 단일 텍스트 (3블록 join)
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger("advisor.llm")

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_PROMPT_VERSION = "v1"


# ──── 기본 시스템 룰/프로파일 (prompts/<ver>/system_*.md 없을 때 fallback) ────

_DEFAULT_RULES = """You are an AI investment advisor for a US stock daytrading system.

CORE RULES (hard constraints — do not violate):
- Position cap: maximum 5 concurrent positions.
- Sector cap: maximum 2 positions per sector.
- Risk-to-reward minimum: 1.5R (1st target).
- Daily loss limits: HALT new entries if account daily PnL <= -3%, CLOSE ALL if <= -5%.
- 2-tier exit: each position is split 50:50 with target_1r (+1R) and target_2r (+2R).
- After 1R hit, stop is raised to breakeven automatically (do not propose otherwise).
- You do NOT execute trades. You produce recommendations; the user approves via Telegram.
- Never propose entry that deviates more than 5% from current price.
- All prices in USD with at most 4 decimal places."""

_DEFAULT_PROFILE = """USER PROFILE:
- Lives in US Eastern Time (NJ). Trades US equities primarily.
- Webull for active trading, Fidelity for long-term hold.
- Uses Python + Next.js custom auto-trading system.
- Active capital: paper account (Alpaca PA3E78E1SMUX). Allocate equity/5 per position.
- Comfortable with daytrading; prefers small/mid-cap momentum + breakout setups.

SYSTEM BACKTEST SUMMARY (as of 2026-05):
- Integrated v10 picks (60-day backfill): 10d alpha +14.88%, win rate 93%, Sharpe 6.58.
- PEAD: post-earnings window alpha +2.56% vs clean-only +1.41% (enter AFTER report ok).
- 2-tier 50:50 partial exit: Sharpe 3~5.8x improvement vs single exit.
- Hybrid dispatch: user_fixed at 09:30, scanner orb_auto evaluated at 09:45."""


class LLMAdvisorClient(ABC):
    """Provider-agnostic LLM client. service.py가 이걸 통해서만 호출."""

    #: provider 이름 (telemetry/로깅용). 서브클래스가 override.
    provider_name: str = "base"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or self._default_model()
        self.prompt_version = prompt_version or self._default_prompt_version()

    @abstractmethod
    def _default_model(self) -> str:
        ...

    def _default_prompt_version(self) -> str:
        import os

        return os.environ.get("ADVISOR_PROMPT_VERSION", DEFAULT_PROMPT_VERSION)

    # ──── 프롬프트 로딩 (공통) ────

    def _load_prompt(self, name: str) -> str:
        path = PROMPT_DIR / self.prompt_version / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"prompt not found: {path}")
        return path.read_text(encoding="utf-8")

    def _read_or_default(self, name: str, default: str) -> str:
        path = PROMPT_DIR / self.prompt_version / f"{name}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return default

    def _system_block_texts(self, prompt_name: str) -> list[str]:
        """3-tier system 블록 (rules / profile / task template)."""
        return [
            self._read_or_default("system_rules", _DEFAULT_RULES),
            self._read_or_default("system_profile", _DEFAULT_PROFILE),
            self._load_prompt(prompt_name),
        ]

    # ──── 호출 (provider별 구현) ────

    @abstractmethod
    async def call(
        self,
        *,
        prompt_name: str,
        user_payload: dict[str, Any] | str,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> tuple[str, dict[str, Any]]:
        """Returns (response_text, usage_dict)."""
        ...

    # ──── 공통 헬퍼 ────

    @staticmethod
    def _to_user_text(payload: dict[str, Any] | str) -> str:
        import json

        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False, default=str, indent=2)
