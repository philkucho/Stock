"use client";

import { useEffect, useState } from "react";

import TopNav from "@/components/TopNav";
import {
  deleteAssignment,
  fetchAssignments,
  fetchPresets,
  fetchSignals,
  saveAssignment,
  type Assignment,
  type Preset,
  type SignalMeta,
} from "@/lib/api";
import HelpDrawer from "./HelpDrawer";

const CATEGORY_COLORS: Record<string, string> = {
  volume: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  trend: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  reversal: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  breakout: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  filter: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
};

export default function StrategiesPage() {
  const [signals, setSignals] = useState<SignalMeta[] | null>(null);
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [assignments, setAssignments] = useState<Assignment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  function reloadAssignments() {
    fetchAssignments()
      .then(setAssignments)
      .catch(() => setAssignments([]));
  }

  useEffect(() => {
    Promise.all([fetchSignals(), fetchPresets()])
      .then(([s, p]) => {
        setSignals(s);
        setPresets(p);
      })
      .catch((e) => setError(String(e)));
    reloadAssignments();
  }, []);

  async function handleToggle(a: Assignment) {
    try {
      await saveAssignment({
        symbol: a.symbol,
        preset_key: a.preset_key,
        enabled: !a.enabled,
      });
      reloadAssignments();
    } catch (e) {
      alert(`Toggle failed: ${e}`);
    }
  }

  async function handleDelete(a: Assignment) {
    if (!confirm(`Delete assignment ${a.symbol} × ${a.preset_key}?`)) return;
    try {
      await deleteAssignment(a.id);
      reloadAssignments();
    } catch (e) {
      alert(`Delete failed: ${e}`);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-6xl space-y-8">
        <TopNav />
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              Strategies & Signals
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              시그널 라이브러리와 거장 스타일 프리셋
            </p>
          </div>
          <nav className="flex gap-2">
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
          </nav>
        </header>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ {error}
          </div>
        )}

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-black dark:text-zinc-50">
            활성 전략 ({assignments?.filter((a) => a.enabled).length ?? 0} / {assignments?.length ?? 0})
          </h2>
          <p className="text-sm text-zinc-500">
            매트릭스에서 적합도 높은 (종목 × 프리셋) 조합을 토글로 활성화. 라이브 매매 단계에선 enabled 만 매매 시그널 생성.
          </p>
          {assignments === null ? (
            <p className="text-zinc-500">Loading…</p>
          ) : assignments.length === 0 ? (
            <p className="rounded-lg border border-zinc-200 bg-white p-4 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900">
              아직 활성 전략 없음. <Link href="/matrix" className="text-blue-600 hover:underline dark:text-blue-400">매트릭스</Link>에서 셀 클릭 → Enable.
            </p>
          ) : (
            <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
              <table className="w-full text-sm">
                <thead className="bg-zinc-100 text-left text-xs uppercase text-zinc-500 dark:bg-zinc-950">
                  <tr>
                    <th className="px-4 py-2">Symbol</th>
                    <th className="px-4 py-2">Preset</th>
                    <th className="px-4 py-2">Notes</th>
                    <th className="px-4 py-2">Updated</th>
                    <th className="px-4 py-2 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                  {assignments.map((a) => (
                    <tr
                      key={a.id}
                      className={a.enabled ? "" : "opacity-50"}
                    >
                      <td className="px-4 py-2 font-mono">{a.symbol}</td>
                      <td className="px-4 py-2">
                        <code className="rounded bg-zinc-100 px-2 py-0.5 text-xs dark:bg-zinc-800">
                          {a.preset_key}
                        </code>
                      </td>
                      <td className="px-4 py-2 text-xs text-zinc-600 dark:text-zinc-400">
                        {a.notes || "—"}
                      </td>
                      <td className="px-4 py-2 text-xs text-zinc-500">
                        {new Date(a.updated_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <button
                          onClick={() => handleToggle(a)}
                          className={`mr-2 rounded px-2 py-1 text-xs ${
                            a.enabled
                              ? "bg-emerald-600 text-white hover:bg-emerald-700"
                              : "border border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
                          }`}
                        >
                          {a.enabled ? "★ Enabled" : "Disabled"}
                        </button>
                        <button
                          onClick={() => handleDelete(a)}
                          className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-black dark:text-zinc-50">
            거장 프리셋
          </h2>
          <p className="text-sm text-zinc-500">
            여러 시그널의 가중합으로 매수/매도 결정. 매트릭스 페이지에서 종목별 적합도를 비교한 후 활성화.
          </p>
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {presets?.map((p) => (
              <PresetCard key={p.key} preset={p} />
            )) ?? <p className="text-zinc-500">Loading…</p>}
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-black dark:text-zinc-50">
            Signal Library ({signals?.length ?? 0})
          </h2>
          <p className="text-sm text-zinc-500">
            각 시그널은 단순 룰. CompositeStrategy가 활성 시그널의 가중합으로 임계값 비교.
          </p>
          <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
            <table className="w-full text-sm">
              <thead className="bg-zinc-100 text-left text-xs uppercase text-zinc-500 dark:bg-zinc-950">
                <tr>
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Category</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="px-4 py-3 text-right">Min bars</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {signals?.map((s) => (
                  <tr key={s.name}>
                    <td className="px-4 py-3 font-mono">{s.name}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`rounded px-2 py-0.5 text-xs ${CATEGORY_COLORS[s.category] ?? CATEGORY_COLORS.filter}`}
                      >
                        {s.category}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-zinc-700 dark:text-zinc-300">
                      {s.description}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-zinc-500">
                      {s.min_bars}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}

function PresetCard({ preset }: { preset: Preset }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-baseline justify-between">
        <h3 className="font-semibold text-black dark:text-zinc-50">{preset.label}</h3>
        <code className="text-xs text-zinc-500">{preset.key}</code>
      </div>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        {preset.description}
      </p>
      <div className="mt-3 flex flex-wrap gap-1">
        {preset.active_signals.map((sig) => (
          <span
            key={sig}
            className="rounded bg-zinc-100 px-2 py-0.5 text-xs font-mono text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
          >
            {sig}
          </span>
        ))}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="text-zinc-500">Buy threshold</dt>
          <dd className="font-mono">
            {preset.buy_threshold.toFixed(1)} / {preset.active_signals.length}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-500">Sell threshold</dt>
          <dd className="font-mono">−{preset.sell_threshold.toFixed(1)}</dd>
        </div>
        <div>
          <dt className="text-zinc-500">Stop loss</dt>
          <dd className="font-mono">{(preset.stop_loss_pct * 100).toFixed(0)}%</dd>
        </div>
        <div>
          <dt className="text-zinc-500">Take profit</dt>
          <dd className="font-mono">{(preset.take_profit_pct * 100).toFixed(0)}%</dd>
        </div>
      </dl>
    </div>
  );
}
