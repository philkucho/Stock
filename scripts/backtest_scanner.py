"""scan_momentum.py 시그널 스코어의 forward-return 백테스트.

매 거래일마다 모든 종목의 점수를 계산하고, 1d/5d/20d 후 수익률을 기록한다.
점수 그룹별 평균 수익률·승률을 NDX 평균 (equal-weight) 벤치마크와 비교해서
"점수가 높을수록 실제로 미래 수익률이 좋은가?"를 확인한다.

사용 예:
    venv/Scripts/python.exe -m scripts.backtest_scanner
    venv/Scripts/python.exe -m scripts.backtest_scanner --start 2024-06-01
    venv/Scripts/python.exe -m scripts.backtest_scanner --json > bt.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

from sqlalchemy import select  # noqa: E402

from api.db import async_session_factory  # noqa: E402
from api.db.models import Bar  # noqa: E402
from signals import SIGNAL_REGISTRY  # noqa: E402
from signals.macro_regime import compute_regime_state, load_macro_bars  # noqa: E402
from scripts.scan_momentum import VOLUME_SIGNALS, MOMENTUM_SIGNALS, ALL_SIGNALS  # noqa: E402

FORWARD_HORIZONS = [1, 5, 20]

# 거래비용 가정: 매수+매도 round-trip 15bps (0.15%).
# Webull commission $0지만 슬리피지+spread 합산 추정.
# 실거래 변동성 큰 날 30bps까지 갈 수 있으므로 알파 마진 얇으면 stress test.
COST_BPS = 15
COST_FRAC = COST_BPS / 10000.0  # 0.0015


async def load_all_bars() -> dict[str, pd.DataFrame]:
    """모든 종목의 1d bar를 한 번에 로드해 메모리 보관 (N+1 쿼리 회피)."""
    async with async_session_factory() as s:
        result = await s.execute(
            select(Bar).where(Bar.interval == "1d").order_by(Bar.symbol, Bar.time)
        )
        rows = result.scalars().all()

    by_symbol: dict[str, list[dict]] = {}
    for b in rows:
        by_symbol.setdefault(b.symbol, []).append({
            "time": b.time,
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
        })
    return {sym: pd.DataFrame(rows).set_index("time") for sym, rows in by_symbol.items()}


def evaluate_full(df: pd.DataFrame) -> pd.DataFrame:
    """종목 전체 기간에 대해 모든 시그널을 한 번에 evaluate (vectorized)."""
    out = pd.DataFrame(index=df.index)
    for name in ALL_SIGNALS:
        out[name] = SIGNAL_REGISTRY[name].evaluate(df)
    out["volume_score"] = out[VOLUME_SIGNALS].clip(lower=0).sum(axis=1)
    out["momentum_score"] = out[MOMENTUM_SIGNALS].clip(lower=0).sum(axis=1)
    out["total_score"] = out["volume_score"] + out["momentum_score"]
    return out


def compute_forward_returns(df: pd.DataFrame) -> pd.DataFrame:
    """각 idx에서 N일 후 close 기준 수익률 + 그 horizon 동안 max drawdown / max favorable excursion.

    각 horizon h에 대해:
      ret_{h}d   : close[t+h]/close[t] - 1                       (gross close-to-close return)
      ret_{h}d_net: ret_{h}d - COST_FRAC                         (15bps round-trip 차감 net)
      mdd_{h}d   : min(low[t+1..t+h])/close[t] - 1               (intra-horizon max drawdown, 음수)
      mfe_{h}d   : max(high[t+1..t+h])/close[t] - 1              (intra-horizon max favorable excursion, 양수)

    mdd는 손절 시뮬에 필수: ret_5d=+1%여도 mdd_5d=-5%였다면 -3% stop이 트리거되어
    실거래 결과가 -3%로 끝났을 것.
    """
    close = df["close"]
    low = df["low"]
    high = df["high"]
    fr = pd.DataFrame(index=df.index)
    for h in FORWARD_HORIZONS:
        ret = close.shift(-h) / close - 1.0
        # Reversed-rolling trick: at index i, give min/max over (i+1, ..., i+h)
        future_min_low = low.iloc[::-1].rolling(window=h, min_periods=h).min().iloc[::-1].shift(-1)
        future_max_high = high.iloc[::-1].rolling(window=h, min_periods=h).max().iloc[::-1].shift(-1)
        fr[f"ret_{h}d"] = ret
        fr[f"ret_{h}d_net"] = ret - COST_FRAC
        fr[f"mdd_{h}d"] = future_min_low / close - 1.0
        fr[f"mfe_{h}d"] = future_max_high / close - 1.0
    return fr


def collect_records(
    all_bars: dict[str, pd.DataFrame],
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    regime_state: pd.Series | None = None,
) -> pd.DataFrame:
    """모든 종목/모든 시점의 시그널 점수 + forward returns 매트릭스.

    Args:
        regime_state: signals.macro_regime.compute_regime_state() 결과.
            제공되면 각 record 행에 `regime_on` 컬럼 추가 (필터링은 호출자가 결정).
            None이면 regime_on 컬럼 생략.
    """
    records: list[pd.DataFrame] = []
    min_bars = max(SIGNAL_REGISTRY[n].min_bars for n in ALL_SIGNALS)

    # 매크로/시그널 종목은 후보 universe에서 제외 (regime gate 데이터로 사용 중)
    # 단순 prefix '^'로 시작하거나 'SPY', 'QQQ', 'VIX' 등은 종목 후보 아님
    macro_skip = {"SPY", "QQQ", "^VIX"}

    for sym, df in all_bars.items():
        if sym in macro_skip:
            continue
        if len(df) < min_bars + max(FORWARD_HORIZONS) + 5:
            continue
        df = df.copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        scores = evaluate_full(df)
        fr = compute_forward_returns(df)

        merged = pd.concat([scores, fr], axis=1)
        merged["symbol"] = sym

        # warmup 구간 + 미래 구간 잘라내기
        merged = merged.iloc[min_bars : len(merged) - max(FORWARD_HORIZONS)]

        if start is not None:
            merged = merged[merged.index >= start]
        if end is not None:
            merged = merged[merged.index <= end]

        if regime_state is not None and not regime_state.empty:
            # 각 record의 시점에 해당하는 regime ON 여부를 broadcast로 매핑
            merged["regime_on"] = regime_state.reindex(merged.index, method="ffill").fillna(False).astype(bool)

        records.append(merged)

    return pd.concat(records, axis=0, ignore_index=False)


def summarize_by_score(records: pd.DataFrame) -> pd.DataFrame:
    """total_score별 forward returns 평균/승률 + 표본 수.

    그로스(`avg_{h}d`)와 net(`avg_{h}d_net`) 둘 다 보고. Sharpe는 net 기준.
    Sharpe annualized 가정: 5d horizon이면 1년에 ~50회, sqrt(50) ≈ 7.07.
    """
    rows = []
    for score in sorted(records["total_score"].unique()):
        sub = records[records["total_score"] == score]
        if sub.empty:
            continue
        row: dict = {"score": int(score), "n": len(sub)}
        for h in FORWARD_HORIZONS:
            ret_col = f"ret_{h}d"
            net_col = f"ret_{h}d_net"
            mdd_col = f"mdd_{h}d"
            row[f"avg_{h}d"] = sub[ret_col].mean()
            row[f"avg_{h}d_net"] = sub[net_col].mean()
            row[f"hit_{h}d"] = (sub[ret_col] > 0).mean()
            row[f"hit_{h}d_net"] = (sub[net_col] > 0).mean()
            std = sub[net_col].std()
            n_per_year = 252.0 / h
            row[f"sharpe_{h}d_net"] = (
                (sub[net_col].mean() / std) * np.sqrt(n_per_year) if std and std > 0 else float("nan")
            )
            row[f"avg_mdd_{h}d"] = sub[mdd_col].mean()  # 평균 intra-horizon drawdown (음수)
        rows.append(row)
    return pd.DataFrame(rows)


def benchmark_returns(records: pd.DataFrame) -> dict[str, float]:
    """전체 표본 (모든 종목, 모든 시점) 평균 = equal-weight universe 근사.

    벤치마크에는 cost를 차감하지 않음 — buy & hold라고 가정.
    시그널 net과 비교해서 alpha 측정.
    """
    out: dict[str, float] = {}
    for h in FORWARD_HORIZONS:
        ret_col = f"ret_{h}d"
        out[f"avg_{h}d"] = records[ret_col].mean()
        out[f"hit_{h}d"] = (records[ret_col] > 0).mean()
    return out


def render_table(summary: pd.DataFrame, bench: dict[str, float], n_total: int) -> str:
    """Net 기준 5d 알파/Sharpe 강조 테이블 (실거래 의사결정용)."""
    lines = [
        f"Forward returns by total_score  (n_total={n_total:,} symbol×day records)  cost={COST_BPS}bps",
        "",
        f"{'SCORE':>5} {'N':>7}  "
        f"{'NET_1D':>8} {'NET_5D':>8} {'NET_20D':>8}  "
        f"{'HIT_5D':>7} {'SHARP_5D':>8}  {'AVG_MDD_5D':>10}",
        "-" * 78,
    ]
    for _, r in summary.iterrows():
        sharpe = r.get("sharpe_5d_net", float("nan"))
        sharpe_str = f"{sharpe:>+7.2f}" if pd.notna(sharpe) else "    n/a"
        lines.append(
            f"{int(r['score']):>5} {int(r['n']):>7}  "
            f"{r['avg_1d_net']*100:>+7.2f}% {r['avg_5d_net']*100:>+7.2f}% {r['avg_20d_net']*100:>+7.2f}%  "
            f"{r['hit_5d_net']*100:>6.1f}% {sharpe_str:>8}  "
            f"{r['avg_mdd_5d']*100:>+9.2f}%"
        )
    lines.append("-" * 78)
    lines.append(
        f"{'BENCH':>5} {n_total:>7}  "
        f"{bench['avg_1d']*100:>+7.2f}% {bench['avg_5d']*100:>+7.2f}% {bench['avg_20d']*100:>+7.2f}%  "
        f"{bench['hit_5d']*100:>6.1f}% {'gross':>8}"
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest scanner score → forward returns")
    p.add_argument("--start", help="시작 날짜 YYYY-MM-DD (default 적재 시작)")
    p.add_argument("--end", help="종료 날짜 YYYY-MM-DD (default 적재 끝)")
    p.add_argument("--json", action="store_true", help="JSON으로 출력")
    p.add_argument(
        "--regime-gate",
        choices=["off", "annotate", "filter"],
        default="annotate",
        help="off: regime 무시 / annotate: regime_on 컬럼만 추가 (default) / filter: regime_on=False 행 제거",
    )
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc)
    print(f"Loading bars from DB...", file=sys.stderr, flush=True)
    all_bars = await load_all_bars()
    print(f"  {len(all_bars)} symbols loaded", file=sys.stderr, flush=True)

    regime_state: pd.Series | None = None
    if args.regime_gate != "off":
        print("Loading macro regime data (SPY/^VIX)...", file=sys.stderr, flush=True)
        macro = await load_macro_bars()
        regime_state = compute_regime_state(macro, fallback_when_missing=True)
        if regime_state.empty or regime_state.eq(True).all():
            print(
                f"  warning: regime data missing or all-ON (universe: {list(macro)}). "
                f"To enforce gate, ingest SPY + ^VIX first.",
                file=sys.stderr, flush=True,
            )
        else:
            n_on = int(regime_state.sum())
            n_off = int((~regime_state).sum())
            print(f"  regime: {n_on} ON / {n_off} OFF days", file=sys.stderr, flush=True)

    start = pd.Timestamp(args.start, tz="UTC") if args.start else None
    end = pd.Timestamp(args.end, tz="UTC") if args.end else None

    print("Computing signals + forward returns...", file=sys.stderr, flush=True)
    records = collect_records(all_bars, start, end, regime_state=regime_state)
    records = records.dropna(subset=[f"ret_{h}d" for h in FORWARD_HORIZONS])
    print(f"  {len(records):,} (symbol × day) records", file=sys.stderr, flush=True)

    if args.regime_gate == "filter" and "regime_on" in records.columns:
        before = len(records)
        records = records[records["regime_on"]]
        print(
            f"  regime filter: {before:,} → {len(records):,} ({before-len(records):,} dropped)",
            file=sys.stderr, flush=True,
        )

    summary = summarize_by_score(records)
    bench = benchmark_returns(records)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    if args.json:
        print(json.dumps({
            "n_total": len(records),
            "by_score": summary.to_dict(orient="records"),
            "benchmark": bench,
            "elapsed_sec": elapsed,
        }, indent=2, default=float))
    else:
        print(render_table(summary, bench, len(records)))
        print(f"\nDone in {elapsed:.1f}s.")

    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
