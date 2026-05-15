"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import TopNav from "@/components/TopNav";
import {
  fetchBacktests,
  type BacktestListResponse,
  type BacktestSummary,
} from "@/lib/api";
import HelpDrawer from "./HelpDrawer";

const PAGE_SIZE = 20;

export default function BacktestsList() {
  const [data, setData] = useState<BacktestListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [helpOpen, setHelpOpen] = useState(false);

  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchBacktests({
      symbol: symbol || undefined,
      strategy_name: strategy || undefined,
      limit: PAGE_SIZE,
      offset,
    })
      .then((d) => setData(d))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [symbol, strategy, offset]);

  function handleApplyFilter(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setOffset(0);
    const fd = new FormData(e.currentTarget);
    setSymbol(((fd.get("symbol") as string) || "").toUpperCase().trim());
    setStrategy(((fd.get("strategy") as string) || "").trim());
  }

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-6xl space-y-6">
        <TopNav />
        <header className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              Backtests
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              저장된 백테스트 결과 ({total}건)
            </p>
          </div>
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
        </header>

        <form
          onSubmit={handleApplyFilter}
          className="flex flex-wrap items-end gap-3 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
        >
          <div className="flex flex-col">
            <label className="mb-1 text-xs text-zinc-500">Symbol</label>
            <input
              name="symbol"
              defaultValue={symbol}
              placeholder="AAPL"
              className="rounded border border-zinc-300 px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950"
            />
          </div>
          <div className="flex flex-col">
            <label className="mb-1 text-xs text-zinc-500">Strategy</label>
            <input
              name="strategy"
              defaultValue={strategy}
              placeholder="SmaCross"
              className="rounded border border-zinc-300 px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950"
            />
          </div>
          <button
            type="submit"
            className="rounded bg-zinc-900 px-4 py-1.5 text-sm text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-black dark:hover:bg-zinc-300"
          >
            Apply
          </button>
          {(symbol || strategy) && (
            <button
              type="button"
              onClick={() => {
                setSymbol("");
                setStrategy("");
                setOffset(0);
              }}
              className="text-sm text-zinc-500 hover:underline"
            >
              Clear
            </button>
          )}
        </form>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ {error}
          </div>
        )}

        {loading && !data ? (
          <p className="text-zinc-500">Loading...</p>
        ) : items.length === 0 ? (
          <p className="rounded-lg border border-zinc-200 bg-white p-8 text-center text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900">
            No backtest runs.{" "}
            <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs dark:bg-zinc-800">
              python -m backtests.run_sma_cross --save
            </code>{" "}
            로 적재하세요.
          </p>
        ) : (
          <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
            <table className="w-full text-sm">
              <thead className="bg-zinc-100 text-left text-xs uppercase text-zinc-500 dark:bg-zinc-950">
                <tr>
                  <th className="px-4 py-3">ID</th>
                  <th className="px-4 py-3">Strategy</th>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Interval</th>
                  <th className="px-4 py-3">Period</th>
                  <th className="px-4 py-3 text-right">PnL</th>
                  <th className="px-4 py-3 text-right">Win%</th>
                  <th className="px-4 py-3 text-right">Trades</th>
                  <th className="px-4 py-3">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {items.map((r) => (
                  <BacktestRow key={r.id} run={r} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {totalPages > 1 && (
          <div className="flex items-center justify-between text-sm text-zinc-500">
            <span>
              Page {page} / {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="rounded border border-zinc-300 px-3 py-1 disabled:opacity-30 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
              >
                ← Prev
              </button>
              <button
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="rounded border border-zinc-300 px-3 py-1 disabled:opacity-30 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
              >
                Next →
              </button>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

function BacktestRow({ run }: { run: BacktestSummary }) {
  const pnl = parseFloat(run.total_pnl);
  const winPct = parseFloat(run.win_rate) * 100;
  return (
    <tr className="hover:bg-zinc-50 dark:hover:bg-zinc-950">
      <td className="px-4 py-3">
        <Link
          href={`/backtests/${run.id}`}
          className="font-mono text-blue-600 hover:underline dark:text-blue-400"
        >
          #{run.id}
        </Link>
      </td>
      <td className="px-4 py-3">{run.strategy_name}</td>
      <td className="px-4 py-3 font-mono">{run.symbol}</td>
      <td className="px-4 py-3 text-zinc-500">{run.interval}</td>
      <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400">
        {run.period_start.slice(0, 10)} ~ {run.period_end.slice(0, 10)}
      </td>
      <td
        className={`px-4 py-3 text-right font-mono ${
          pnl > 0
            ? "text-emerald-600 dark:text-emerald-400"
            : pnl < 0
              ? "text-red-600 dark:text-red-400"
              : ""
        }`}
      >
        ${pnl.toFixed(2)}
      </td>
      <td className="px-4 py-3 text-right font-mono">{winPct.toFixed(1)}%</td>
      <td className="px-4 py-3 text-right font-mono">{run.total_positions}</td>
      <td className="px-4 py-3 text-xs text-zinc-500">
        {new Date(run.created_at).toLocaleString()}
      </td>
    </tr>
  );
}
