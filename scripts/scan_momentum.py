"""거래량 깔때기 + 가격 모멘텀 종목 스캐너 (Phase 1, 일봉 기반).

종합 점수 = volume_trend + (가격 모멘텀 시그널들 합).
점수 내림차순으로 종목 랭킹.

사용 예:
    venv/Scripts/python.exe -m scripts.scan_momentum
    venv/Scripts/python.exe -m scripts.scan_momentum --top 30
    venv/Scripts/python.exe -m scripts.scan_momentum --date 2026-04-30
    venv/Scripts/python.exe -m scripts.scan_momentum --json > today.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

from sqlalchemy import select  # noqa: E402

from api.db import async_session_factory  # noqa: E402
from api.db.models import Bar  # noqa: E402
from signals import SIGNAL_REGISTRY  # noqa: E402
from signals.macro_regime import compute_regime_state, is_regime_on, load_macro_bars  # noqa: E402


VOLUME_SIGNALS = ["volume_trend"]
MOMENTUM_SIGNALS = ["ma_alignment", "rsi_bullish", "macd", "above_ma200", "breakout_20d"]
ALL_SIGNALS = VOLUME_SIGNALS + MOMENTUM_SIGNALS

# 매크로/지수 종목은 후보 universe에서 제외 (regime 데이터로 사용 중)
MACRO_SKIP = {"SPY", "QQQ", "^VIX"}

EARNINGS_CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "earnings_calendar.json"
DEFAULT_EARNINGS_BLACKOUT_DAYS = 5


async def list_symbols() -> list[str]:
    """매크로/지수 종목 (SPY, QQQ, ^VIX) 제외하고 1d bar 적재된 종목 리스트."""
    async with async_session_factory() as s:
        result = await s.execute(select(Bar.symbol).distinct().order_by(Bar.symbol))
        return [row[0] for row in result.all() if row[0] not in MACRO_SKIP]


def load_earnings_calendar(path: Path) -> dict[str, dict] | None:
    """data/earnings_calendar.json → {symbol: {next, past}} dict.

    파일 부재 시 None 반환 (호출자가 silent skip).
    """
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload.get("earnings", {})


def earnings_phase(
    symbol: str,
    target_date: date,
    earnings: dict[str, dict],
    days: int = DEFAULT_EARNINGS_BLACKOUT_DAYS,
) -> str:
    """target_date 기준 종목의 earnings 단계 분류.

    Returns:
        'pre'   : 진입 이후 ±0~+days 내에 earnings → binary risk (회피 권장)
        'post'  : 진입 이전 -days~-1일 내에 earnings → PEAD drift 윈도우 (진입 OK, 알파 강)
        'clean' : earnings 없음 / 데이터 없음 → 순수 모멘텀

    days는 캘린더일 기준. target_date 기준 오늘 발표(diff=0)는 보수적으로 'pre'.
    """
    sym_data = earnings.get(symbol)
    if not sym_data:
        return "clean"
    candidates: list[str] = []
    if sym_data.get("next"):
        candidates.append(sym_data["next"])
    candidates.extend(sym_data.get("past", []))

    nearest_diff: int | None = None
    for ds in candidates:
        try:
            ed = date.fromisoformat(ds)
        except ValueError:
            continue
        diff = (ed - target_date).days  # +면 미래, -면 과거
        if abs(diff) <= days:
            if nearest_diff is None or abs(diff) < abs(nearest_diff):
                nearest_diff = diff
    if nearest_diff is None:
        return "clean"
    if nearest_diff >= 0:  # today + future = pre (binary risk)
        return "pre"
    return "post"  # past = PEAD


def is_in_earnings_blackout(
    symbol: str,
    target_date: date,
    earnings: dict[str, dict],
    days: int = DEFAULT_EARNINGS_BLACKOUT_DAYS,
) -> bool:
    """[하위호환] target_date가 earnings ±days 내면 True. 새 코드는 earnings_phase 사용 권장."""
    return earnings_phase(symbol, target_date, earnings, days) != "clean"


async def fetch_bars(symbol: str) -> pd.DataFrame:
    async with async_session_factory() as s:
        result = await s.execute(
            select(Bar).where(Bar.symbol == symbol, Bar.interval == "1d").order_by(Bar.time)
        )
        rows = result.scalars().all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "time": b.time,
        "open": float(b.open),
        "high": float(b.high),
        "low": float(b.low),
        "close": float(b.close),
        "volume": float(b.volume),
    } for b in rows]).set_index("time")


def evaluate_at_date(bars: pd.DataFrame, target_date: date | None) -> dict | None:
    """target_date(또는 가장 최근 거래일) 기준 시그널 평가. 데이터 부족하면 None."""
    if bars.empty:
        return None
    bars_utc = bars.copy()
    if bars_utc.index.tz is None:
        bars_utc.index = bars_utc.index.tz_localize("UTC")

    if target_date is None:
        idx = len(bars_utc) - 1
    else:
        target_ts = pd.Timestamp(target_date, tz="UTC")
        on_or_before = bars_utc.index <= target_ts + pd.Timedelta(days=1)
        if not on_or_before.any():
            return None
        idx = int(on_or_before.sum()) - 1

    if idx < max(spec.min_bars for spec in (SIGNAL_REGISTRY[n] for n in ALL_SIGNALS)):
        return None

    actual_date = bars_utc.index[idx].date()
    scores: dict[str, int] = {}
    for name in ALL_SIGNALS:
        spec = SIGNAL_REGISTRY[name]
        scores[name] = int(spec.evaluate(bars_utc).iloc[idx])

    vol_score = sum(scores[n] for n in VOLUME_SIGNALS if scores[n] > 0)
    mom_score = sum(scores[n] for n in MOMENTUM_SIGNALS if scores[n] > 0)
    last_close = float(bars_utc["close"].iloc[idx])
    last_volume = float(bars_utc["volume"].iloc[idx])
    avg20_volume = float(bars_utc["volume"].iloc[max(0, idx - 19): idx + 1].mean())

    return {
        "as_of": actual_date.isoformat(),
        "close": last_close,
        "volume": int(last_volume),
        "vol_vs_20d_avg": round(last_volume / avg20_volume, 2) if avg20_volume else None,
        "signals": scores,
        "volume_score": vol_score,
        "momentum_score": mom_score,
        "total_score": vol_score + mom_score,
    }


async def scan(target_date: date | None) -> tuple[list[dict], dict]:
    """전 종목 스캔.

    Returns:
        (ranked, meta) — meta는 regime/earnings 컨텍스트.
        meta = {"as_of": date, "regime_on": bool|None}
    """
    symbols = await list_symbols()
    out: list[dict] = []
    for sym in symbols:
        bars = await fetch_bars(sym)
        result = evaluate_at_date(bars, target_date)
        if result is None:
            continue
        result["symbol"] = sym
        out.append(result)
    out.sort(key=lambda r: (r["total_score"], r["volume_score"], r["momentum_score"]), reverse=True)

    # regime 컨텍스트 (DB 데이터 있으면 채움)
    regime_on: bool | None = None
    if out:
        try:
            macro = await load_macro_bars()
            state = compute_regime_state(macro, fallback_when_missing=False)
            if not state.empty:
                ref_date = target_date if target_date else date.fromisoformat(out[0]["as_of"])
                regime_on = is_regime_on(state, ref_date)
        except Exception:  # noqa: BLE001
            regime_on = None

    return out, {"regime_on": regime_on}


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILTER_PATH = PROJECT_ROOT / "data" / "symbol_filter.json"


def load_filter(path: Path) -> dict[str, dict] | None:
    """data/symbol_filter.json 로드. 종목별 historical 통계를 dict로 반환.

    Returns:
        {symbol: {n, avg_ret, hit_rate, group}} or None if file missing
        group ∈ {"whitelist", "blacklist", "unknown"}
    """
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for group in ("whitelist", "blacklist", "unknown"):
        for r in payload.get(group, []):
            out[r["symbol"]] = {**r, "group": group}
    return out


def render_table(ranked: list[dict], top: int, filter_data: dict | None) -> str:
    rows = ranked[:top]
    if not rows:
        return "(no candidates)"

    show_phase = any("earnings_phase" in r for r in rows)
    phase_emoji = {"pre": "🚨", "post": "📈", "clean": "  "}

    if filter_data is not None:
        header = (
            f"{'#':>3} {'SYM':<6} {'GRP':<3} {'EAR':<5} {'CLOSE':>9} {'VOL/20d':>8} "
            f"{'VT':>3} {'MA':>3} {'RSI':>4} {'MAC':>4} {'M200':>5} {'BRK':>4} {'TOT':>4} "
            f"{'H_HIT':>6} {'H_AVG':>7}"
        ) if show_phase else (
            f"{'#':>3} {'SYM':<6} {'GRP':<3} {'CLOSE':>9} {'VOL/20d':>8} "
            f"{'VT':>3} {'MA':>3} {'RSI':>4} {'MAC':>4} {'M200':>5} {'BRK':>4} {'TOT':>4} "
            f"{'H_HIT':>6} {'H_AVG':>7}"
        )
    else:
        header = (
            f"{'#':>3} {'SYM':<6} {'CLOSE':>9} {'VOL/20d':>8} "
            f"{'VT':>3} {'MA':>3} {'RSI':>4} {'MAC':>4} {'M200':>5} {'BRK':>4} {'TOT':>4}"
        )
    sep = "-" * len(header)
    lines = [f"As of {rows[0]['as_of']}  (showing top {len(rows)} of {len(ranked)})", "", header, sep]
    for i, r in enumerate(rows, 1):
        s = r["signals"]
        phase = r.get("earnings_phase", "clean")
        phase_str = (f"{phase_emoji.get(phase, '?')}{phase[:3]:<3} ") if show_phase else ""
        base = (
            f"{i:>3} {r['symbol']:<6} "
            + (f"{filter_data[r['symbol']]['group'][:3].upper():<3} " if filter_data and r['symbol'] in filter_data else ("---  " if filter_data else ""))
            + (phase_str if filter_data is not None else "")
            + f"{r['close']:>9.2f} {(r['vol_vs_20d_avg'] or 0):>7.2f}x"
            + f" {s['volume_trend']:>3} {s['ma_alignment']:>3} {s['rsi_bullish']:>4}"
            + f" {s['macd']:>4} {s['above_ma200']:>5} {s['breakout_20d']:>4} {r['total_score']:>4}"
        )
        if filter_data is not None and r["symbol"] in filter_data:
            f = filter_data[r["symbol"]]
            base += f" {f['hit_rate']*100:>5.1f}% {f['avg_ret']*100:>+6.2f}%"
        elif filter_data is not None:
            base += f" {'---':>6} {'---':>7}"
        lines.append(base)
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Volume funnel + price momentum scanner")
    p.add_argument("--top", type=int, default=20, help="상위 N개 출력 (default 20)")
    p.add_argument("--date", help="기준 날짜 YYYY-MM-DD (default: 가장 최근 거래일)")
    p.add_argument("--json", action="store_true", help="JSON 형식으로 전체 결과 출력")
    p.add_argument("--min-score", type=int, default=1, help="최소 total_score (default 1)")
    p.add_argument(
        "--filter",
        nargs="?",
        const=str(DEFAULT_FILTER_PATH),
        default=None,
        help=f"심볼 필터 JSON 경로 적용. 인자 생략 시 {DEFAULT_FILTER_PATH.relative_to(PROJECT_ROOT)} 사용",
    )
    p.add_argument(
        "--filter-mode",
        choices=["whitelist-only", "no-blacklist", "annotate"],
        default="annotate",
        help="whitelist-only: WHITELIST만 통과 / no-blacklist: BLACKLIST만 제외 / annotate: 모두 표시 + 그룹 표기 (default)",
    )
    p.add_argument(
        "--regime-gate",
        choices=["off", "annotate", "block"],
        default="annotate",
        help="off: 무시 / annotate: regime 상태 표시만 (default) / block: regime OFF면 후보 0건 반환",
    )
    p.add_argument(
        "--earnings-blackout",
        choices=["off", "annotate", "exclude", "pre_only"],
        default="pre_only",
        help=(
            "off: 무시 / "
            "annotate: 표시만 / "
            "exclude: ±5일 모두 제외 (보수적) / "
            "pre_only: 발표 전(0~+5)만 차단, 발표 후(-5~-1) PEAD 진입 OK (default, 권장)"
        ),
    )
    p.add_argument(
        "--earnings-blackout-days",
        type=int,
        default=DEFAULT_EARNINGS_BLACKOUT_DAYS,
        help=f"earnings 양옆 캘린더일 윈도우 (default {DEFAULT_EARNINGS_BLACKOUT_DAYS})",
    )
    return p.parse_args()


def apply_filter(ranked: list[dict], filter_data: dict, mode: str) -> list[dict]:
    if mode == "whitelist-only":
        return [r for r in ranked if filter_data.get(r["symbol"], {}).get("group") == "whitelist"]
    if mode == "no-blacklist":
        return [r for r in ranked if filter_data.get(r["symbol"], {}).get("group") != "blacklist"]
    return ranked  # annotate


def main() -> int:
    args = parse_args()
    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    filter_data = load_filter(Path(args.filter)) if args.filter else None
    if args.filter and filter_data is None:
        print(f"warning: filter file not found at {args.filter}", file=sys.stderr)

    earnings_data: dict[str, dict] | None = None
    if args.earnings_blackout != "off":
        earnings_data = load_earnings_calendar(EARNINGS_CALENDAR_PATH)
        if earnings_data is None:
            print(
                f"warning: earnings calendar not found at {EARNINGS_CALENDAR_PATH.relative_to(PROJECT_ROOT)}. "
                "Run `python -m scripts.ingest_earnings_calendar --universe ndx100` to generate.",
                file=sys.stderr,
            )

    started = datetime.now(timezone.utc)
    ranked, meta = asyncio.run(scan(target))
    regime_on = meta.get("regime_on")
    ranked = [r for r in ranked if r["total_score"] >= args.min_score]

    # earnings 처리: phase 분류 (pre/post/clean) 후 모드별 필터링
    if earnings_data is not None and args.earnings_blackout != "off":
        for r in ranked:
            phase = earnings_phase(
                r["symbol"], date.fromisoformat(r["as_of"]), earnings_data, args.earnings_blackout_days
            )
            r["earnings_phase"] = phase  # 'pre' | 'post' | 'clean'
            r["in_earnings_blackout"] = phase != "clean"  # 하위호환
        if args.earnings_blackout == "exclude":
            ranked = [r for r in ranked if r["earnings_phase"] == "clean"]
        elif args.earnings_blackout == "pre_only":
            ranked = [r for r in ranked if r["earnings_phase"] != "pre"]

    if filter_data is not None:
        ranked = apply_filter(ranked, filter_data, args.filter_mode)

    # regime block 처리
    if args.regime_gate == "block" and regime_on is False:
        ranked = []

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    if args.json:
        if filter_data is not None:
            for r in ranked:
                if r["symbol"] in filter_data:
                    r["historical"] = {
                        k: filter_data[r["symbol"]][k]
                        for k in ("group", "n", "avg_ret", "hit_rate", "median_ret")
                    }
        out_payload = {
            "candidates": ranked,
            "meta": {
                "regime_on": regime_on,
                "earnings_blackout_mode": args.earnings_blackout,
                "as_of": ranked[0]["as_of"] if ranked else None,
            },
        }
        print(json.dumps(out_payload, indent=2, ensure_ascii=False))
    else:
        # regime banner
        regime_str = "ON" if regime_on is True else ("OFF" if regime_on is False else "n/a")
        regime_emoji = "✅" if regime_on is True else ("🛑" if regime_on is False else "❓")
        print(f"Market regime (SPY > MA200 + VIX < 25): {regime_emoji} {regime_str}")
        if regime_on is False and args.regime_gate == "annotate":
            print("  ⚠️ regime OFF — 후보 표시되어도 진입 비추천. --regime-gate=block 으로 강제 차단 가능.")
        print()
        print(render_table(ranked, args.top, filter_data))
        msg = f"\nScanned in {elapsed:.1f}s. {len(ranked)} candidates with score ≥ {args.min_score}"
        flags = []
        if filter_data is not None:
            flags.append(f"filter={args.filter_mode}")
        if earnings_data is not None and args.earnings_blackout != "off":
            n_blackout = sum(1 for r in ranked if r.get("in_earnings_blackout"))
            flags.append(f"earnings_blackout={args.earnings_blackout}({n_blackout} flagged)")
        if args.regime_gate != "off":
            flags.append(f"regime_gate={args.regime_gate}")
        if flags:
            msg += f"  [{', '.join(flags)}]"
        print(msg + ".")

    return 0


if __name__ == "__main__":
    sys.exit(main())
