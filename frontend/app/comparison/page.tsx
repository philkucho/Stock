"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type ReactElement } from "react";

import {
  fetchCompareSummary,
  fetchCompareToday,
  triggerCompareBackfill,
  triggerCompareLog,
  type CompareSummaryResponse,
  type CompareTodayResponse,
  type ComparePickLog,
  type SystemKPI,
} from "@/lib/api";
import TopNav from "@/components/TopNav";
import HelpDrawer from "./HelpDrawer";

// 6-way 분리: integrated는 score_meta.source 기준 v10 / v9_fallback,
// 대시보드는 자체 Tier 평가 파이프라인, intraday는 단타 5-Model Stack
const SYSTEM_LABEL: Record<string, string> = {
  v3: "v3 Daily Picks",
  scanner: "scan_momentum",
  integrated_v10: "통합 v10 (기본)",
  integrated_v9_fallback: "통합 v9 (fallback)",
  dashboard: "대시보드 (Tier)",
  intraday: "단타 5-Model",
};

const SYSTEM_BADGE: Record<string, string> = {
  v3: "v3",
  scanner: "scanner",
  integrated_v10: "v10",
  integrated_v9_fallback: "v9 fallback",
  dashboard: "dashboard",
  intraday: "intraday",
};

// 그래프 곡선 색과 동일: v3=파랑, scanner=검정, v10=빨강, v9=호박, dashboard=보라, intraday=분홍
const SYSTEM_COLOR: Record<string, string> = {
  v3: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  scanner: "bg-zinc-200 text-zinc-900 dark:bg-zinc-700 dark:text-zinc-100",
  integrated_v10: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  integrated_v9_fallback: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  dashboard: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
  intraday: "bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300",
};

const HORIZONS = [1, 5, 10] as const;

export default function ComparisonPage() {
  const [today, setToday] = useState<CompareTodayResponse | null>(null);
  const [summary, setSummary] = useState<CompareSummaryResponse | null>(null);
  const [days, setDays] = useState(2);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  async function load() {
    setError(null);
    try {
      const [t, s] = await Promise.all([fetchCompareToday(), fetchCompareSummary(days)]);
      setToday(t);
      setSummary(s);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  async function onLogToday() {
    setBusy("log");
    setError(null);
    try {
      await triggerCompareLog();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function onBackfill() {
    setBusy("backfill");
    setError(null);
    try {
      await triggerCompareBackfill();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-7xl space-y-6">
        <TopNav />
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              📊 시스템 비교
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              5 시스템(v3 / scan_momentum / 통합 v10 / 통합 v9 fallback / 대시보드) 매일 picks의 1일/5일/10일 후 실현 수익 비교 — 각 시스템 $10,000 균등 5분배 시뮬레이션. 통합은 v10 부족 시 v9로 보충된 부분을 따로 추적, 대시보드는 Tier 분류 자체 파이프라인.
            </p>
          </div>
          <nav className="flex flex-wrap gap-2">
            <button
              onClick={() => setHelpOpen(!helpOpen)}
              className={`rounded-lg border px-3 py-2 text-sm transition-colors ${
                helpOpen
                  ? "border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-400 dark:bg-blue-950 dark:text-blue-300"
                  : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
              }`}
            >
              ❓ 도움말
            </button>
            <button
              onClick={onLogToday}
              disabled={busy !== null}
              className="rounded-lg border border-blue-300 bg-blue-50 px-3 py-2 text-sm text-blue-700 hover:bg-blue-100 disabled:opacity-50 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300"
              title="오늘 3시스템 picks 로깅 (개장 직전 자동 호출)"
            >
              {busy === "log" ? "기록 중…" : "↻ 오늘 picks 기록"}
            </button>
            <button
              onClick={onBackfill}
              disabled={busy !== null}
              className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300"
              title="1d/5d/10d outcome 백필 (장 마감 후 자동 호출)"
            >
              {busy === "backfill" ? "백필 중…" : "🔄 결과 백필"}
            </button>
          </nav>
        </header>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ {error}
          </div>
        )}

        {/* 시스템별 KPI 카드 */}
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-black dark:text-zinc-50">
              누적 통계 (최근 {days}일)
            </h2>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            >
              {[1, 2, 7, 14, 30, 60, 90].map((d) => (
                <option key={d} value={d}>
                  최근 {d}일
                </option>
              ))}
            </select>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
            {summary?.systems.map((s) => (
              <SystemCard key={s.system_id} kpi={s} />
            ))}
          </div>
        </section>

        {/* 누적 PnL 곡선 (간단 sparkline) */}
        {summary && (
          <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-3 text-lg font-semibold text-black dark:text-zinc-50">
              누적 PnL 곡선 — 5일 보유 기준 ($)
            </h2>
            <PnlChart curves={summary.cumulative_pnl_curve} />
          </section>
        )}

        {/* 오늘 picks 비교 */}
        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-black dark:text-zinc-50">
            오늘 picks 비교 ({today?.pick_date ?? "—"})
          </h2>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
            {Object.entries(today?.by_system ?? {}).map(([sys, picks]) => (
              <SystemPicksColumn key={sys} systemId={sys} picks={picks} />
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}

function SystemCard({ kpi }: { kpi: SystemKPI }) {
  const label = SYSTEM_LABEL[kpi.system_id] ?? kpi.system_id;
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-baseline justify-between">
        <h3 className="font-semibold text-black dark:text-zinc-50">{label}</h3>
        <span className={`rounded px-2 py-0.5 text-xs ${SYSTEM_COLOR[kpi.system_id] ?? ""}`}>
          {SYSTEM_BADGE[kpi.system_id] ?? kpi.system_id}
        </span>
      </div>
      <p className="mt-1 text-xs text-zinc-500">
        총 picks {kpi.n_picks} · outcome 있는 picks {kpi.n_with_outcome}
      </p>
      <div className="mt-3 space-y-2">
        {HORIZONS.map((h) => {
          const k = kpi.horizon_kpis[String(h)] ?? kpi.horizon_kpis[h];
          if (!k) return null;
          return (
            <div key={h} className="rounded bg-zinc-50 p-2 dark:bg-zinc-950">
              <div className="flex items-baseline justify-between text-xs">
                <span className="font-semibold">
                  {h}일 보유 <span className="text-zinc-500">(n={k.n})</span>
                </span>
                <span className={k.total_pnl_usd >= 0 ? "text-emerald-600" : "text-red-600"}>
                  {k.total_pnl_usd >= 0 ? "+" : ""}${k.total_pnl_usd.toFixed(2)}
                </span>
              </div>
              <dl className="mt-1 grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px]">
                <Row label="평균 수익" value={`${k.avg_return_pct >= 0 ? "+" : ""}${k.avg_return_pct.toFixed(2)}%`} positive={k.avg_return_pct > 0} />
                <Row label="SPY 알파" value={`${k.avg_alpha_pct >= 0 ? "+" : ""}${k.avg_alpha_pct.toFixed(2)}%`} positive={k.avg_alpha_pct > 0} />
                <Row label="승률" value={`${(k.win_rate * 100).toFixed(0)}%`} />
                <Row label="알파 승률" value={`${(k.win_alpha_rate * 100).toFixed(0)}%`} />
                <Row label="Sharpe" value={k.sharpe.toFixed(2)} positive={k.sharpe > 1} />
                <Row label="최대/최소" value={`${k.max_return_pct.toFixed(1)}/${k.min_return_pct.toFixed(1)}%`} />
              </dl>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Row({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  const color = positive ? "text-emerald-600 dark:text-emerald-400" : "text-zinc-700 dark:text-zinc-300";
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className={`text-right font-mono ${color}`}>{value}</dd>
    </>
  );
}

function SystemPicksColumn({ systemId, picks }: { systemId: string; picks: ComparePickLog[] }) {
  const label = SYSTEM_LABEL[systemId] ?? systemId;
  return (
    <div className="rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
      <div className="border-b border-zinc-200 p-3 dark:border-zinc-800">
        <h3 className="font-semibold text-black dark:text-zinc-50">
          {label}{" "}
          <span className="text-xs text-zinc-500">({picks.length})</span>
        </h3>
      </div>
      <div className="space-y-2 p-3">
        {picks.length === 0 && (
          <p className="text-xs text-zinc-500">picks 없음</p>
        )}
        {picks.map((p) => (
          <PickRow key={p.id} pick={p} />
        ))}
      </div>
    </div>
  );
}

function PickRow({ pick }: { pick: ComparePickLog }) {
  const score = parseFloat(pick.score);
  const o5 = pick.outcomes.find((o) => o.horizon_days === 5);
  return (
    <div className="rounded border border-zinc-100 bg-zinc-50 p-2 text-xs dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-baseline justify-between">
        <div>
          <span className="text-zinc-500">#{pick.rank}</span>{" "}
          <span className="font-bold">{pick.symbol}</span>
          {pick.sector && <span className="ml-2 text-[10px] text-zinc-500">{pick.sector}</span>}
        </div>
        <span className="font-mono text-blue-600 dark:text-blue-400">{score.toFixed(1)}</span>
      </div>
      {pick.entry_price && (
        <p className="mt-1 text-[11px] text-zinc-500">
          진입가 ${parseFloat(pick.entry_price).toFixed(2)}
          {pick.strategy_tag && (
            <span className="ml-2 rounded bg-zinc-200 px-1 dark:bg-zinc-800">{pick.strategy_tag}</span>
          )}
        </p>
      )}
      {pick.outcomes.length > 0 && (
        <div className="mt-1.5 grid grid-cols-3 gap-1 text-[11px]">
          {[1, 5, 10].map((h) => {
            const o = pick.outcomes.find((x) => x.horizon_days === h);
            if (!o) {
              return (
                <div key={h} className="rounded bg-zinc-100 p-1 text-center text-zinc-400 dark:bg-zinc-900">
                  {h}d 대기
                </div>
              );
            }
            const pct = parseFloat(o.pct_return);
            const alpha = parseFloat(o.alpha);
            return (
              <div
                key={h}
                className={`rounded p-1 text-center ${
                  pct > 0
                    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                    : "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                }`}
                title={`SPY 대비 알파: ${alpha >= 0 ? "+" : ""}${alpha.toFixed(2)}%`}
              >
                <div className="text-[10px] text-zinc-500">{h}d</div>
                <div className="font-mono font-semibold">
                  {pct >= 0 ? "+" : ""}
                  {pct.toFixed(1)}%
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PnlChart({ curves }: { curves: Record<string, { date: string; cum_pnl_usd: number }[]> }) {
  // 단순 inline SVG sparkline
  const allPoints = useMemo(() => {
    const sets: { sys: string; points: { date: string; cum_pnl_usd: number }[] }[] = [];
    Object.entries(curves).forEach(([sys, pts]) => {
      if (pts.length > 0) sets.push({ sys, points: pts });
    });
    return sets;
  }, [curves]);

  if (allPoints.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        아직 outcome 데이터가 없습니다 — 5일 후 결과가 백필되면 곡선 표시.
      </p>
    );
  }

  // 모든 곡선 통합 y 범위
  const allY = allPoints.flatMap((s) => s.points.map((p) => p.cum_pnl_usd));
  const minY = Math.min(...allY, 0);
  const maxY = Math.max(...allY, 0);
  const yRange = maxY - minY || 1;
  const maxX = Math.max(...allPoints.map((s) => s.points.length));
  const W = 800;
  const H = 220;
  const PAD = 30;

  function xCoord(i: number, n: number): number {
    if (n <= 1) return PAD;
    return PAD + (i / (n - 1)) * (W - 2 * PAD);
  }
  function yCoord(v: number): number {
    return H - PAD - ((v - minY) / yRange) * (H - 2 * PAD);
  }

  const colors: Record<string, string> = {
    v3: "#2563eb",                    // 파랑
    scanner: "#111827",               // 검정
    integrated_v10: "#dc2626",        // 빨강 (가장 강조)
    integrated_v9_fallback: "#f59e0b",// 호박 (v10 보조)
    dashboard: "#8b5cf6",             // 보라
    intraday: "#ec4899",              // 분홍 (단타)
  };

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        {/* zero line */}
        <line
          x1={PAD}
          y1={yCoord(0)}
          x2={W - PAD}
          y2={yCoord(0)}
          stroke="currentColor"
          strokeOpacity="0.2"
          strokeDasharray="4 2"
          className="text-zinc-400"
        />
        {/* axes labels */}
        <text x={PAD} y={PAD - 8} className="fill-zinc-500 text-[10px]">
          ${maxY.toFixed(0)}
        </text>
        <text x={PAD} y={H - 8} className="fill-zinc-500 text-[10px]">
          ${minY.toFixed(0)}
        </text>

        {allPoints.map((s) => {
          const path = s.points
            .map((p, i) => `${i === 0 ? "M" : "L"} ${xCoord(i, s.points.length)} ${yCoord(p.cum_pnl_usd)}`)
            .join(" ");
          return (
            <g key={s.sys}>
              <path
                d={path}
                fill="none"
                stroke={colors[s.sys] ?? "#71717a"}
                strokeWidth="2"
              />
              {s.points.map((p, i) => (
                <circle
                  key={i}
                  cx={xCoord(i, s.points.length)}
                  cy={yCoord(p.cum_pnl_usd)}
                  r="2.5"
                  fill={colors[s.sys] ?? "#71717a"}
                >
                  <title>
                    {s.sys} {p.date}: ${p.cum_pnl_usd.toFixed(2)}
                  </title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex flex-wrap gap-3 text-xs">
        {allPoints.map((s) => {
          const last = s.points[s.points.length - 1];
          return (
            <div key={s.sys} className="flex items-center gap-1.5">
              <span
                className="inline-block h-2 w-3"
                style={{ backgroundColor: colors[s.sys] ?? "#71717a" }}
              />
              <span className="font-medium">{SYSTEM_LABEL[s.sys] ?? s.sys}</span>
              <span className="font-mono text-zinc-500">
                ${last.cum_pnl_usd.toFixed(0)} ({s.points.length}d)
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
