"""Walk-forward 분할 헬퍼.

용어:
- train 기간: (이번 MVP에선 미사용 — 시그널·임계값 자동 튜닝 단계 대비)
- test 기간:  적합도 평가용. 모든 메트릭은 이 기간만 사용.
- warmup 기간: test_start 이전, 시그널 인디케이터 워밍업용 (200일 SMA 등).
              데이터는 가져오되 거래·메트릭 계산엔 포함 안 함.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

WARMUP_DAYS = 260  # 1년치 거래일 (200일 SMA + 여유)


@dataclass(frozen=True)
class WalkForwardSpec:
    train_start: str  # YYYY-MM-DD
    train_end: str
    test_start: str
    test_end: str

    def __post_init__(self) -> None:
        for label, val in [
            ("train_start", self.train_start),
            ("train_end", self.train_end),
            ("test_start", self.test_start),
            ("test_end", self.test_end),
        ]:
            try:
                datetime.strptime(val, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"{label}={val!r} not YYYY-MM-DD") from exc

        if self.train_end > self.test_start:
            raise ValueError(
                f"train_end ({self.train_end}) must be <= test_start ({self.test_start})"
            )
        if self.train_start >= self.train_end:
            raise ValueError("train_start must be < train_end")
        if self.test_start >= self.test_end:
            raise ValueError("test_start must be < test_end")

    @property
    def warmup_start(self) -> str:
        """test_start 이전 워밍업 시작일. train_start가 충분히 앞이면 그 값 사용."""
        wm = (
            datetime.strptime(self.test_start, "%Y-%m-%d") - timedelta(days=WARMUP_DAYS * 1.5)
        ).strftime("%Y-%m-%d")
        return min(wm, self.train_start)

    def filter_test_period(self, df: pd.DataFrame) -> pd.DataFrame:
        """DataFrame에서 test 기간만 슬라이스 (UTC 인덱스 가정)."""
        s = pd.Timestamp(self.test_start, tz="UTC")
        e = pd.Timestamp(self.test_end, tz="UTC")
        return df.loc[s:e]


def parse_period_arg(s: str) -> tuple[str, str]:
    """CLI 인자 'YYYY-MM-DD:YYYY-MM-DD' → (start, end) 튜플."""
    if ":" not in s:
        raise ValueError(f"Period must be 'start:end' (got {s!r})")
    a, b = s.split(":", 1)
    return a.strip(), b.strip()
