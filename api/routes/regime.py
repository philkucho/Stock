"""시장 regime 진단 + 알림 — multi-horizon hit rate 기반.

핵심 가설: mean_reversion hit rate가 70% 이하로 내려가면 횡보장 약화/추세장 전환 신호.
- RANGING        : mean_reversion 강세, trend 약함
- TRENDING       : trend 강세, mean_reversion 약함
- MIXED          : 둘 다 일정 수준
- TRANSITION     : 우세 프리셋 hit rate가 명확히 떨어지는 중
- WEAK_MARKET    : 모두 hit rate 30% 미만

매트릭스 parquet에서 직접 계산. DB 읽기 없음.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel

from backtests.data_cache import REPO_ROOT

router = APIRouter()

MATRIX_PARQUET = REPO_ROOT / "data" / "matrix_runs.parquet"

# hit-rate 임계값 — fitness > 이 값을 "양호"로 카운트
HIT_THRESHOLD = 0.3
# regime 분류 임계값
RANGING_HIT = 0.65       # mean_reversion hit_rate >= 0.65 → 횡보장 후보
TRENDING_HIT = 0.65      # trend_follow / cis_style 강할 때
WEAK_HIT = 0.30          # 모두 이 미만이면 시장 약체


class PresetStats(BaseModel):
    preset_key: str
    avg_fitness: float
    median_fitness: float
    hit_rate: float           # fitness > 0.3 비율
    sample_n: int


class HorizonSnapshot(BaseModel):
    label: str
    period_start: str
    period_end: str
    presets: list[PresetStats]
    dominant: str | None       # 가장 hit rate 높은 프리셋
    weakest: str | None
    cell_count: int


class RegimeAlert(BaseModel):
    severity: str              # info / warning / critical
    code: str
    message: str


class RegimeResponse(BaseModel):
    regime: str                # RANGING / TRENDING / MIXED / TRANSITION_TO_TRENDING / TRANSITION_TO_RANGING / WEAK_MARKET
    description: str
    snapshots: list[HorizonSnapshot]
    alerts: list[RegimeAlert]
    recommendation: str


def _per_preset_stats(df: pd.DataFrame) -> list[PresetStats]:
    if df.empty:
        return []
    df = df.drop_duplicates(["symbol", "preset_key"])
    rows: list[PresetStats] = []
    for preset, sub in df.groupby("preset_key"):
        n = len(sub)
        rows.append(
            PresetStats(
                preset_key=str(preset),
                avg_fitness=round(float(sub["fitness"].mean()), 4),
                median_fitness=round(float(sub["fitness"].median()), 4),
                hit_rate=round(float((sub["fitness"] > HIT_THRESHOLD).sum() / n), 4),
                sample_n=int(n),
            )
        )
    return sorted(rows, key=lambda r: r.hit_rate, reverse=True)


def _snapshot(
    df_all: pd.DataFrame, period_start: str, period_end: str, label: str
) -> HorizonSnapshot | None:
    sub = df_all[
        (df_all.period_start == period_start) & (df_all.period_end == period_end)
    ]
    if sub.empty:
        return None
    presets = _per_preset_stats(sub)
    dominant = presets[0].preset_key if presets else None
    weakest = presets[-1].preset_key if presets else None
    return HorizonSnapshot(
        label=label,
        period_start=period_start,
        period_end=period_end,
        presets=presets,
        dominant=dominant,
        weakest=weakest,
        cell_count=len(sub),
    )


def _classify_regime(snapshot: HorizonSnapshot) -> tuple[str, str]:
    """단일 horizon에서 regime 분류."""
    by_key = {p.preset_key: p for p in snapshot.presets}
    mr = by_key.get("mean_reversion")
    tf = by_key.get("trend_follow")
    cs = by_key.get("cis_style")

    if not (mr and tf):
        return "UNKNOWN", "프리셋 데이터 부족"

    # 모두 약하면 약체장
    max_hit = max(p.hit_rate for p in snapshot.presets)
    if max_hit < WEAK_HIT:
        return (
            "WEAK_MARKET",
            f"모든 프리셋 hit rate {max_hit*100:.0f}% 미만 — 시장 자체가 시그널 빈약",
        )

    # 평균회귀 우세
    if mr.hit_rate >= RANGING_HIT and tf.hit_rate < 0.50:
        return (
            "RANGING",
            f"mean_reversion {mr.hit_rate*100:.0f}% vs trend_follow {tf.hit_rate*100:.0f}% — 횡보장",
        )

    # 추세 우세 (trend_follow 또는 cis_style 강세)
    trend_hit = max(tf.hit_rate, cs.hit_rate if cs else 0)
    if trend_hit >= TRENDING_HIT and mr.hit_rate < 0.50:
        return (
            "TRENDING",
            f"trend/cis {trend_hit*100:.0f}% vs mean_reversion {mr.hit_rate*100:.0f}% — 추세장",
        )

    return (
        "MIXED",
        f"mean_reversion {mr.hit_rate*100:.0f}%, trend_follow {tf.hit_rate*100:.0f}% — 혼재",
    )


def _detect_transition(
    snaps: list[HorizonSnapshot],
) -> tuple[str | None, list[RegimeAlert]]:
    """1M / 3M / 12M 비교로 전환 감지."""
    alerts: list[RegimeAlert] = []
    if len(snaps) < 2:
        return None, alerts

    # 짧은 → 긴 순서로 (가장 짧은 = 최근)
    snaps_sorted = sorted(
        snaps, key=lambda s: pd.Timestamp(s.period_end) - pd.Timestamp(s.period_start)
    )

    def hit(snap: HorizonSnapshot, key: str) -> float | None:
        for p in snap.presets:
            if p.preset_key == key:
                return p.hit_rate
        return None

    short = snaps_sorted[0]
    longest = snaps_sorted[-1]

    mr_short = hit(short, "mean_reversion")
    mr_long = hit(longest, "mean_reversion")
    tf_short = hit(short, "trend_follow")
    tf_long = hit(longest, "trend_follow")

    transition: str | None = None

    if mr_short is not None and mr_long is not None:
        delta = mr_short - mr_long
        if delta < -0.20:
            alerts.append(
                RegimeAlert(
                    severity="warning",
                    code="MR_WEAKENING",
                    message=(
                        f"mean_reversion hit rate가 {short.label}에서 "
                        f"{mr_short*100:.0f}%로 {longest.label} {mr_long*100:.0f}% 대비 "
                        f"{delta*100:+.0f}%p 약화 — 횡보장 끝 신호 가능"
                    ),
                )
            )
            transition = "TRANSITION_TO_TRENDING"
        elif mr_short < 0.5 and mr_long >= 0.7:
            alerts.append(
                RegimeAlert(
                    severity="critical",
                    code="MR_BREAK",
                    message=(
                        f"mean_reversion hit rate가 {short.label}에서 50% 미만 "
                        f"({mr_short*100:.0f}%) — 추세장 진입 강한 신호"
                    ),
                )
            )
            transition = "TRANSITION_TO_TRENDING"

    if tf_short is not None and tf_long is not None:
        delta = tf_short - tf_long
        if delta > 0.20 and tf_short >= 0.65:
            alerts.append(
                RegimeAlert(
                    severity="warning",
                    code="TF_RISING",
                    message=(
                        f"trend_follow hit rate가 {short.label} {tf_short*100:.0f}%로 "
                        f"{longest.label} {tf_long*100:.0f}% 대비 {delta*100:+.0f}%p 상승 "
                        "— 추세장 부상 가능"
                    ),
                )
            )
            transition = "TRANSITION_TO_TRENDING"
        elif delta < -0.25:
            alerts.append(
                RegimeAlert(
                    severity="info",
                    code="TF_FADING",
                    message=(
                        f"trend_follow가 {short.label}에서 약화 ({tf_short*100:.0f}%) — "
                        "추세 소멸, mean_reversion 우세 강화"
                    ),
                )
            )
            transition = "TRANSITION_TO_RANGING"

    return transition, alerts


@router.get("/", response_model=RegimeResponse)
async def get_regime(
    h12_start: str | None = Query(default=None),
    h12_end: str | None = Query(default=None),
    h3_start: str | None = Query(default=None),
    h3_end: str | None = Query(default=None),
    h1_start: str | None = Query(default=None),
    h1_end: str | None = Query(default=None),
) -> RegimeResponse:
    """시장 regime 진단.

    파라미터를 안 주면 가장 최근 12M / 3M / 1M 매트릭스를 자동 추정.
    """
    if not MATRIX_PARQUET.exists():
        return RegimeResponse(
            regime="UNKNOWN",
            description="매트릭스 데이터 없음",
            snapshots=[],
            alerts=[
                RegimeAlert(
                    severity="critical",
                    code="NO_DATA",
                    message="data/matrix_runs.parquet 없음 — run_matrix 먼저 실행",
                )
            ],
            recommendation="python -m scripts.monthly_refresh",
        )

    df = pd.read_parquet(MATRIX_PARQUET)
    if df.empty:
        return RegimeResponse(
            regime="UNKNOWN",
            description="매트릭스 비어있음",
            snapshots=[],
            alerts=[],
            recommendation="run_matrix 실행",
        )

    # 자동 매칭: 사용자가 안 주면 가장 최근 12M / 3M / 1M
    df["len_days"] = (
        pd.to_datetime(df["period_end"]) - pd.to_datetime(df["period_start"])
    ).dt.days
    periods = (
        df.drop_duplicates(["period_start", "period_end"])
        .sort_values("period_end", ascending=False)
    )

    def _pick(target: int, tol: int) -> tuple[str, str] | None:
        candidates = periods[
            (periods.len_days >= target - tol) & (periods.len_days <= target + tol)
        ]
        if candidates.empty:
            return None
        # period_end 가장 최근
        row = candidates.iloc[0]
        return str(row["period_start"]), str(row["period_end"])

    if not (h12_start and h12_end):
        pick = _pick(360, 40)
        h12_start, h12_end = pick if pick else (None, None)
    if not (h3_start and h3_end):
        pick = _pick(90, 30)
        h3_start, h3_end = pick if pick else (None, None)
    if not (h1_start and h1_end):
        pick = _pick(30, 10)
        h1_start, h1_end = pick if pick else (None, None)

    snaps: list[HorizonSnapshot] = []
    for label, ps, pe in [
        ("12M", h12_start, h12_end),
        ("3M", h3_start, h3_end),
        ("1M", h1_start, h1_end),
    ]:
        if ps and pe:
            snap = _snapshot(df, ps, pe, label)
            if snap:
                snaps.append(snap)

    if not snaps:
        return RegimeResponse(
            regime="UNKNOWN",
            description="유효한 매트릭스 기간 없음",
            snapshots=[],
            alerts=[],
            recommendation="run_matrix 실행",
        )

    # 가장 짧은 horizon (최근) 기반 regime 분류
    snaps_sorted = sorted(
        snaps, key=lambda s: pd.Timestamp(s.period_end) - pd.Timestamp(s.period_start)
    )
    primary_regime, primary_desc = _classify_regime(snaps_sorted[0])

    # transition 감지
    transition, transition_alerts = _detect_transition(snaps)
    final_regime = transition or primary_regime

    # mean_reversion 우세 시 분산 위험 추가 알림
    by_key_short = {p.preset_key: p for p in snaps_sorted[0].presets}
    mr_short = by_key_short.get("mean_reversion")
    if mr_short and mr_short.hit_rate >= 0.70:
        transition_alerts.append(
            RegimeAlert(
                severity="info",
                code="MR_CONCENTRATION_RISK",
                message=(
                    f"mean_reversion 활성 페어가 다수일 가능성 높음 "
                    f"(hit rate {mr_short.hit_rate*100:.0f}%) — 분산 점검 권장"
                ),
            )
        )

    # 추천 액션
    if final_regime == "RANGING":
        rec = "현재 활성 전략의 mean_reversion 비중 유지. trend_follow/cis_style은 보조."
    elif final_regime == "TRENDING":
        rec = "활성 전략을 trend_follow/cis_style 중심으로 재정비 검토."
    elif final_regime in ("TRANSITION_TO_TRENDING",):
        rec = "mean_reversion 의존도를 줄이고 추세 시그널 모니터링. 매트릭스 재실행 권장."
    elif final_regime == "TRANSITION_TO_RANGING":
        rec = "추세 시그널 약화, 평균회귀 비중 확대 가능."
    elif final_regime == "WEAK_MARKET":
        rec = "전반적 시그널 빈약 — 활성 전략 축소, 라이브 노출 최소화 권장."
    else:
        rec = "현재 분포 유지하며 다음 매트릭스 갱신 시 재평가."

    return RegimeResponse(
        regime=final_regime,
        description=primary_desc,
        snapshots=snaps,
        alerts=transition_alerts,
        recommendation=rec,
    )
