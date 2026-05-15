"""3 시스템 picks 비교 추적 모듈.

- adapters.py — v3 / scan_momentum / integrated 통합 인터페이스
- logger.py — 매일 09:30 ET 직전 picks 기록
- outcomes.py — 매일 16:30 ET 후 1d/5d/10d 결과 백필
"""

SYSTEMS = ("v3", "scanner", "integrated", "dashboard", "intraday")
# 시뮬·표시용 effective system: integrated는 score_meta.source 기준 v10 / v9_fallback 분리.
# source 태그가 없는 historical 행은 v10으로 가정 (v10이 기본, v9는 fallback).
# intraday는 단타 5-Model Stack (preopen + ORB confirm) 결과.
EFFECTIVE_SYSTEMS = (
    "v3",
    "scanner",
    "integrated_v10",
    "integrated_v9_fallback",
    "dashboard",
    "intraday",
)
SIM_CAPITAL_PER_SYSTEM = 10_000.0
TOP_N = 5
HOLDING_HORIZONS = (1, 5, 10)
BENCHMARK_SYMBOL = "SPY"


def effective_system_id(system_id: str, score_meta: dict | None) -> str:
    """raw system_id + score_meta → 표시용 effective system_id.

    integrated를 v10 / v9_fallback 두 버킷으로 분해. source 태그 누락 시 v10.
    """
    if system_id != "integrated":
        return system_id
    src = (score_meta or {}).get("source")
    if src == "v9_fallback":
        return "integrated_v9_fallback"
    return "integrated_v10"
