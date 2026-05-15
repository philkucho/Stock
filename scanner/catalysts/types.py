"""카탈리스트 공용 타입 — 순환 import 방지를 위해 별도 모듈."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CatalystKind(str, Enum):
    EARNINGS = "earnings"
    FDA_MA = "fda_ma"
    UPGRADE = "upgrade"
    NEWS = "news"
    NONE = "none"


KIND_SCORE: dict[CatalystKind, int] = {
    CatalystKind.EARNINGS: 5,
    CatalystKind.FDA_MA: 4,
    CatalystKind.UPGRADE: 3,
    CatalystKind.NEWS: 1,
    CatalystKind.NONE: 0,
}


@dataclass
class CatalystHit:
    kind: CatalystKind
    headline: str
    source: str
    url: str | None = None


@dataclass
class CatalystScore:
    score: int  # 0~5
    primary_kind: CatalystKind
    summary: str
    source: str
    all_hits: list[CatalystHit] = field(default_factory=list)
