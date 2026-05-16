"use client";

import { useEffect, useMemo, useState } from "react";

import TopNav from "@/components/TopNav";
import {
  fetchSourcesSummary,
  type CumulativePoint,
  type SourcePath,
  type SourceTradeRow,
  type SourcesSummaryResponse,
} from "@/lib/api";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// ── 경로 라벨/색상 정의 (UX P1: 한글 우선) ────────────────────────────
const PATH_LABEL: Record<SourcePath, string> = {
  user_fixed: "사용자 입력",
  orb_auto: "ORB 자동",
  advisor: "AI 자문",
};

const PATH_DESC: Record<SourcePath, string> = {
  user_fixed: "/trading 페이지에서 직접 입력 → 09:30 자동 발송",
  orb_auto: "스캐너 자동 watchlist → 09:45 ORB 4-pass 통과 시 발송",
  advisor: "AI 자문 추천 → Telegram 승인 즉시 발송",
};

const PATH_COLOR: Record<SourcePath, string> = {
  user_fixed: "#2563eb", // blue-600
  orb_auto: "#dc2626", // red-600
  advisor: "#7c3aed", // violet-600
};

const PATH_BADGE: Record<SourcePath, string> = {
  user_fixed:
    "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300 border-blue-300 dark:border-blue-800",
  orb_auto:
    "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300 border-red-300 dark:border-red-800",
  advisor:
    "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300 border-violet-300 dark:border-violet-800",
};

const RANGE_OPTIONS = [7, 30, 90, 180] as const;

function fmtUsd(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}$${v.toFixed(2)}`;
}
function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}
function fmtRate(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}
function pnlClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return "text-zinc-500";
  return v > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
}

// ── Summary card per path ───────────────────────────────────────────────
function PathSummaryCard({
  path,
  data,
}: {
  path: SourcePath;
  data: SourcesSummaryResponse["summary"][SourcePath];
}) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold" style={{ color: PATH_COLOR[path] }}>
            {PATH_LABEL[path]}
          </h3>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {PATH_DESC[path]}
          </p>
        </div>
        <span
          className={`rounded-full border px-2 py-0.5 text-xs font-medium ${PATH_BADGE[path]}`}
          title={path}
        >
          {data.n_plans} plan
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
        <dt className="text-zinc-500 dark:text-zinc-400">발송 / 결과</dt>
        <dd className="text-right font-mono">
          {data.n_sent} / {data.n_with_outcome}
        </dd>

        <dt className="text-zinc-500 dark:text-zinc-400">승률</dt>
        <dd className="text-right font-mono">{fmtRate(data.win_rate)}</dd>

        <dt className="text-zinc-500 dark:text-zinc-400">평균 수익률</dt>
        <dd
          className={`text-right font-mono ${pnlClass(data.avg_return_pct)}`}
        >
          {fmtPct(data.avg_return_pct)}
        </dd>

        <dt
          className="text-zinc-500 dark:text-zinc-400"
          title="SPY 대비 초과 수익률"
        >
          평균 알파
        </dt>
        <dd className={`text-right font-mono ${pnlClass(data.avg_alpha_pct)}`}>
          {fmtPct(data.avg_alpha_pct)}
        </dd>

        <dt className="text-zinc-500 dark:text-zinc-400">누적 손익</dt>
        <dd
          className={`text-right font-mono font-bold ${pnlClass(data.total_pnl_usd)}`}
        >
          {fmtUsd(data.total_pnl_usd)}
        </dd>

        <dt
          className="text-zinc-500 dark:text-zinc-400"
          title="1차 목표가 도달 비율"
        >
          1차 목표 도달
        </dt>
        <dd className="text-right font-mono text-emerald-600 dark:text-emerald-400">
          {fmtRate(data.hit_t1_rate)}
        </dd>

        <dt
          className="text-zinc-500 dark:text-zinc-400"
          title="2차 목표가 도달 비율"
        >
          2차 목표 도달
        </dt>
        <dd className="text-right font-mono text-emerald-600 dark:text-emerald-400">
          {fmtRate(data.hit_t2_rate)}
        </dd>

        <dt
          className="text-zinc-500 dark:text-zinc-400"
          title="손절가 도달 비율"
        >
          손절 발동
        </dt>
        <dd className="text-right font-mono text-rose-600 dark:text-rose-400">
          {fmtRate(data.hit_stop_rate)}
        </dd>
      </dl>
    </div>
  );
}

// ── Cumulative PnL chart ───────────────────────────────────────────────
function CumulativeChart({ data }: { data: CumulativePoint[] }) {
  if (data.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-zinc-500">
        결과 데이터가 아직 없습니다 — 발송된 거래의 1일 후 종가가 backfill되면
        표시됩니다.
      </div>
    );
  }
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 10, right: 24, bottom: 10, left: 0 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="#404040"
            strokeOpacity={0.25}
          />
          <XAxis dataKey="date" fontSize={11} />
          <YAxis
            tickFormatter={(v: number) =>
              `$${v >= 0 ? "" : "-"}${Math.abs(v).toFixed(0)}`
            }
            fontSize={11}
          />
          <Tooltip
            formatter={(v: number, name: string) => [
              `$${v.toFixed(2)}`,
              PATH_LABEL[name as SourcePath] ?? name,
            ]}
            labelClassName="text-zinc-900 dark:text-zinc-100"
          />
          <Legend
            formatter={(v: string) => PATH_LABEL[v as SourcePath] ?? v}
          />
          <Line
            type="monotone"
            dataKey="user_fixed"
            stroke={PATH_COLOR.user_fixed}
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="orb_auto"
            stroke={PATH_COLOR.orb_auto}
            strokeWidth={2}
            dot={false}
            strokeDasharray="5 3"
          />
          <Line
            type="monotone"
            dataKey="advisor"
            stroke={PATH_COLOR.advisor}
            strokeWidth={2}
            dot={false}
            strokeDasharray="2 2"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Trade list table ───────────────────────────────────────────────────
function TradeList({
  trades,
  filter,
}: {
  trades: SourceTradeRow[];
  filter: SourcePath | "all";
}) {
  const filtered = useMemo(
    () => (filter === "all" ? trades : trades.filter((t) => t.path === filter)),
    [trades, filter],
  );

  if (filtered.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700">
        해당 경로의 거래 내역이 없습니다.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-50 text-left text-xs uppercase text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
          <tr>
            <th className="px-3 py-2">날짜</th>
            <th className="px-3 py-2">종목</th>
            <th className="px-3 py-2">경로</th>
            <th className="px-3 py-2">상태</th>
            <th className="px-3 py-2 text-right">주식수</th>
            <th className="px-3 py-2 text-right">진입가</th>
            <th
              className="px-3 py-2 text-right text-rose-600 dark:text-rose-400"
              title="손절가"
            >
              손절
            </th>
            <th className="px-3 py-2 text-right">청산가</th>
            <th className="px-3 py-2 text-right">수익률</th>
            <th
              className="px-3 py-2 text-right"
              title="SPY 대비 초과 수익률"
            >
              알파
            </th>
            <th className="px-3 py-2 text-right">손익</th>
            <th className="px-3 py-2 text-center" title="목표/손절 도달">
              결과
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {filtered.map((t) => {
            const hitMark = (() => {
              if (t.hit_target_2r) return "🎯🎯";
              if (t.hit_target_1r) return "🎯";
              if (t.hit_stop) return "🛑";
              if (t.exit_date) return "⏱";
              return "—";
            })();
            return (
              <tr
                key={t.plan_id}
                className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
              >
                <td className="px-3 py-2 font-mono text-xs">{t.plan_date}</td>
                <td className="px-3 py-2 font-bold">{t.symbol}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded border px-1.5 py-0.5 text-xs font-medium ${PATH_BADGE[t.path]}`}
                    title={t.path}
                  >
                    {PATH_LABEL[t.path]}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs">
                  <span
                    className={
                      t.confirm_status === "sent"
                        ? "text-emerald-600 dark:text-emerald-400"
                        : t.confirm_status === "failed"
                          ? "text-rose-600 dark:text-rose-400"
                          : "text-zinc-500"
                    }
                  >
                    {t.confirm_status}
                  </span>
                </td>
                <td className="px-3 py-2 text-right font-mono">{t.qty}</td>
                <td className="px-3 py-2 text-right font-mono">
                  ${Number(t.entry_price).toFixed(2)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-rose-600 dark:text-rose-400">
                  ${Number(t.stop_price).toFixed(2)}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {t.exit_price ? `$${Number(t.exit_price).toFixed(2)}` : "—"}
                </td>
                <td
                  className={`px-3 py-2 text-right font-mono ${pnlClass(t.pct_return)}`}
                >
                  {fmtPct(t.pct_return)}
                </td>
                <td
                  className={`px-3 py-2 text-right font-mono ${pnlClass(t.alpha_pct)}`}
                >
                  {fmtPct(t.alpha_pct)}
                </td>
                <td
                  className={`px-3 py-2 text-right font-mono font-bold ${pnlClass(t.partial_pnl_usd)}`}
                >
                  {fmtUsd(t.partial_pnl_usd)}
                </td>
                <td
                  className="px-3 py-2 text-center"
                  title={
                    t.hit_target_2r
                      ? "2차 목표 도달"
                      : t.hit_target_1r
                        ? "1차 목표 도달"
                        : t.hit_stop
                          ? "손절 발동"
                          : t.exit_date
                            ? "보유기간 종료 청산"
                            : "결과 대기중"
                  }
                >
                  {hitMark}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────
export default function SourcesPage() {
  const [days, setDays] = useState<number>(30);
  const [horizon, setHorizon] = useState<number>(1);
  const [filter, setFilter] = useState<SourcePath | "all">("all");
  const [data, setData] = useState<SourcesSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchSourcesSummary(days, horizon);
      setData(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, horizon]);

  const paths: SourcePath[] = ["user_fixed", "orb_auto", "advisor"];

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <div className="mx-auto max-w-7xl space-y-6">
        <TopNav />
        <header>
          <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
            🚦 진입 경로별 성과
          </h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            3가지 자동매매 진입 경로(사용자 입력 / ORB 자동 / AI 자문)의 실제
            발송 거래와 실현 수익을 비교합니다. 결과는 <strong>모의투자(paper)</strong>{" "}
            기준이며, 1일/5일/10일 후 종가로 산출됩니다.
          </p>
        </header>

        {/* 조회 컨트롤 */}
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950">
          <div className="flex items-center gap-2">
            <span className="text-sm text-zinc-500 dark:text-zinc-400">
              기간:
            </span>
            {RANGE_OPTIONS.map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`rounded-md border px-3 py-1 text-sm transition ${
                  days === d
                    ? "border-zinc-800 bg-zinc-900 text-white dark:border-zinc-200 dark:bg-zinc-100 dark:text-black"
                    : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
                }`}
              >
                {d}일
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-zinc-500 dark:text-zinc-400">
              보유기간:
            </span>
            {[1, 5, 10].map((h) => (
              <button
                key={h}
                onClick={() => setHorizon(h)}
                className={`rounded-md border px-3 py-1 text-sm transition ${
                  horizon === h
                    ? "border-zinc-800 bg-zinc-900 text-white dark:border-zinc-200 dark:bg-zinc-100 dark:text-black"
                    : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
                }`}
              >
                {h}일
              </button>
            ))}
          </div>
          <div className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
            {data && `${data.range_start} ~ ${data.range_end}`}
          </div>
        </div>

        {/* 상태 메시지 */}
        {loading && (
          <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300">
            거래 내역 불러오는 중…
          </div>
        )}
        {error && (
          <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-300">
            ⚠️ 불러오기 실패: {error}
          </div>
        )}

        {/* Summary 카드 3개 */}
        {data && (
          <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {paths.map((p) => (
              <PathSummaryCard key={p} path={p} data={data.summary[p]} />
            ))}
          </section>
        )}

        {/* Cumulative PnL chart */}
        {data && (
          <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
            <h2 className="mb-3 text-lg font-bold text-zinc-900 dark:text-zinc-100">
              📈 누적 손익 추이
            </h2>
            <CumulativeChart data={data.cumulative_pnl} />
          </section>
        )}

        {/* Trade list */}
        {data && (
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-100">
                📋 거래 내역 ({data.trades.length}건)
              </h2>
              <div className="flex items-center gap-2">
                <span className="text-sm text-zinc-500 dark:text-zinc-400">
                  경로 필터:
                </span>
                {(["all", ...paths] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    className={`rounded-md border px-2.5 py-1 text-xs font-medium transition ${
                      filter === f
                        ? "border-zinc-800 bg-zinc-900 text-white dark:border-zinc-200 dark:bg-zinc-100 dark:text-black"
                        : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
                    }`}
                  >
                    {f === "all" ? "전체" : PATH_LABEL[f]}
                  </button>
                ))}
              </div>
            </div>
            <TradeList trades={data.trades} filter={filter} />
          </section>
        )}

        {/* 범례 */}
        <footer className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          🔎 <strong>현재 모의투자(paper) 단계</strong> · 결과 컬럼: 🎯 1차
          목표 · 🎯🎯 2차 목표 · 🛑 손절 · ⏱ 보유기간 종료 청산 · — 결과 대기
        </footer>
      </div>
    </main>
  );
}
