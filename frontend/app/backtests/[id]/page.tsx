"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import TopNav from "@/components/TopNav";
import { fetchBacktest, type BacktestRun } from "@/lib/api";
import HelpDrawer from "./HelpDrawer";

// recharts는 turbopack SSR과 충돌 가능 → 클라이언트 전용 dynamic import
const PnlBarChart = dynamic(() => import("@/components/PnlBarChart"), {
  ssr: false,
  loading: () => <div className="h-64 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800" />,
});

export default function BacktestDetail() {
  const params = useParams<{ id: string }>();
  const id = Number(params?.id);

  const [run, setRun] = useState<BacktestRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    if (!Number.isFinite(id)) {
      setError(`Invalid id: ${params?.id}`);
      setLoading(false);
      return;
    }
    fetchBacktest(id)
      .then(setRun)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [id, params?.id]);

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-4xl space-y-6">
        <TopNav />
        <header className="flex items-center justify-between gap-2">
          <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
            Backtest #{Number.isFinite(id) ? id : "?"}
          </h1>
          <div className="flex gap-2">
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
            <Link
              href="/backtests"
              className="rounded-lg border border-zinc-300 px-3 py-2 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
            >
              ← All backtests
            </Link>
          </div>
        </header>

        {loading && <p className="text-zinc-500">Loading...</p>}
        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ {error}
          </div>
        )}

        {run && <BacktestDetailBody run={run} />}
      </div>
    </main>
  );
}

function BacktestDetailBody({ run }: { run: BacktestRun }) {
  const totalPnl = parseFloat(run.total_pnl);
  const winPct = parseFloat(run.win_rate) * 100;
  const startCash = parseFloat(run.starting_cash);
  const finalEquity = parseFloat(run.final_equity);
  const returnPct = ((finalEquity - startCash) / startCash) * 100;

  const m = run.metrics ?? {};
  const chartData = [
    { name: "Best", value: m.best_position_pnl ?? 0, fill: "#10b981" },
    { name: "Avg", value: m.avg_position_pnl ?? 0, fill: "#6366f1" },
    { name: "Worst", value: m.worst_position_pnl ?? 0, fill: "#ef4444" },
  ];

  return (
    <>
      <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="mb-4 flex items-baseline gap-3">
          <span className="text-2xl font-semibold">
            {run.strategy_name} · {run.symbol}
          </span>
          <span className="text-sm text-zinc-500">{run.interval}</span>
        </div>
        <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <Stat
            label="Total PnL"
            value={`$${totalPnl.toFixed(2)}`}
            tone={totalPnl > 0 ? "pos" : totalPnl < 0 ? "neg" : "neutral"}
          />
          <Stat
            label="Return"
            value={`${returnPct >= 0 ? "+" : ""}${returnPct.toFixed(2)}%`}
            tone={returnPct > 0 ? "pos" : returnPct < 0 ? "neg" : "neutral"}
          />
          <Stat label="Win Rate" value={`${winPct.toFixed(1)}%`} />
          <Stat label="Trades" value={String(run.total_positions)} />
        </div>
      </section>

      {(m.best_position_pnl !== undefined ||
        m.avg_position_pnl !== undefined ||
        m.worst_position_pnl !== undefined) && (
        <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-3 text-lg font-semibold">Position PnL distribution</h2>
          <PnlBarChart data={chartData} />
        </section>
      )}

      <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="mb-3 text-lg font-semibold">Run details</h2>
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <Field label="Period">
            {run.period_start.slice(0, 10)} ~ {run.period_end.slice(0, 10)}
          </Field>
          <Field label="Data source">
            <code className="rounded bg-zinc-100 px-2 py-0.5 text-xs dark:bg-zinc-800">
              {run.data_source}
            </code>
          </Field>
          <Field label="Starting cash">${startCash.toLocaleString()}</Field>
          <Field label="Final equity">${finalEquity.toLocaleString()}</Field>
          <Field label="Wins / Losses">
            <span className="text-emerald-600 dark:text-emerald-400">
              {run.wins}W
            </span>{" "}
            /{" "}
            <span className="text-red-600 dark:text-red-400">{run.losses}L</span>
          </Field>
          <Field label="Total fills">{run.total_fills}</Field>
          <Field label="Created">{new Date(run.created_at).toLocaleString()}</Field>
          {run.notes && <Field label="Notes">{run.notes}</Field>}
        </dl>
      </section>

      <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="mb-3 text-lg font-semibold">Strategy parameters</h2>
        <pre className="overflow-x-auto rounded bg-zinc-100 p-3 text-xs dark:bg-zinc-950">
          {JSON.stringify(run.strategy_params, null, 2)}
        </pre>
      </section>
    </>
  );
}

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "neutral";
}) {
  const color =
    tone === "pos"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "neg"
        ? "text-red-600 dark:text-red-400"
        : "text-black dark:text-zinc-50";
  return (
    <div>
      <dt className="text-xs uppercase text-zinc-500">{label}</dt>
      <dd className={`mt-1 font-mono text-2xl ${color}`}>{value}</dd>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase text-zinc-500">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}
