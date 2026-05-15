"use client";

import { useEffect, useMemo, useState } from "react";

import TopNav from "@/components/TopNav";
import {
  fetchAssignments,
  fetchMatrix,
  fetchMatrixPeriods,
  fetchRegime,
  saveAssignment,
  triggerMatrixRun,
  type Assignment,
  type MatrixCell,
  type MatrixPeriod,
  type MatrixResponse,
  type RegimeResponse,
} from "@/lib/api";
import HelpDrawer from "./HelpDrawer";

type CellKey = string; // `${symbol}|${preset_key}`

type TrustCategory = "ROBUST" | "OVERFIT" | "DECAYED" | "WEAK" | "NO_DATA";

function cellKey(symbol: string, preset: string): CellKey {
  return `${symbol}|${preset}`;
}

function fitnessColor(f: number): string {
  const clamped = Math.max(-0.5, Math.min(1.5, f));
  const norm = (clamped + 0.5) / 2.0;
  const hue = 120 * norm;
  return `hsl(${hue}, 65%, 45%)`;
}

function classifyTrust(train: number | undefined, test: number): TrustCategory {
  if (train === undefined) return "NO_DATA";
  if (train >= 0.3 && test >= 0.3) return "ROBUST";
  if (train < 0.2 && test > 0.5) return "OVERFIT";
  if (train > 0.5 && test < 0.2) return "DECAYED";
  return "WEAK";
}

const TRUST_DOT: Record<TrustCategory, string> = {
  ROBUST: "bg-blue-500",
  OVERFIT: "bg-red-500",
  DECAYED: "bg-black dark:bg-white",
  WEAK: "bg-zinc-400",
  NO_DATA: "",
};

const TRUST_LABEL: Record<TrustCategory, string> = {
  ROBUST: "두 기간 모두 양호 (신뢰)",
  OVERFIT: "검증기간만 좋음 (실거래 위험)",
  DECAYED: "학습기간만 좋음 (시장 변화로 망가짐)",
  WEAK: "두 기간 모두 약함",
  NO_DATA: "학습기간 데이터 없음",
};

function periodLengthDays(p: MatrixPeriod): number {
  const a = new Date(p.period_start).getTime();
  const b = new Date(p.period_end).getTime();
  return Math.round((b - a) / 86400000);
}

// 12M test end 기준으로 가장 가까운 3M / 1M 매트릭스 자동 매칭
function findHorizon(
  periods: MatrixPeriod[],
  anchorEnd: string,
  targetDays: number,
  tolerance: number,
): MatrixPeriod | null {
  // period_end ≈ anchorEnd 이고 길이가 targetDays ± tolerance 인 매트릭스
  let best: MatrixPeriod | null = null;
  let bestScore = Infinity;
  for (const p of periods) {
    const len = periodLengthDays(p);
    if (Math.abs(len - targetDays) > tolerance) continue;
    const endDiff = Math.abs(
      new Date(p.period_end).getTime() - new Date(anchorEnd).getTime(),
    ) / 86400000;
    if (endDiff > 45) continue; // 직전 한 달 정도 차이까지만
    const score = endDiff * 2 + Math.abs(len - targetDays);
    if (score < bestScore) {
      bestScore = score;
      best = p;
    }
  }
  return best;
}

export default function MatrixPage() {
  const [periods, setPeriods] = useState<MatrixPeriod[] | null>(null);
  const [periodIdx, setPeriodIdx] = useState(0);
  const [trainPeriodIdx, setTrainPeriodIdx] = useState<number>(-1); // -1 = off
  const [matrix, setMatrix] = useState<MatrixResponse | null>(null);
  const [trainMatrix, setTrainMatrix] = useState<MatrixResponse | null>(null);
  const [h3Matrix, setH3Matrix] = useState<MatrixResponse | null>(null);
  const [h1Matrix, setH1Matrix] = useState<MatrixResponse | null>(null);
  const [multiHorizon, setMultiHorizon] = useState(false);
  const [assignments, setAssignments] = useState<Assignment[] | null>(null);
  const [trustMode, setTrustMode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [selected, setSelected] = useState<MatrixCell | null>(null);
  const [regime, setRegime] = useState<RegimeResponse | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    fetchRegime()
      .then(setRegime)
      .catch(() => setRegime(null));
  }, []);

  // Initial load: periods + auto-pick a different period as train (if any)
  useEffect(() => {
    fetchMatrixPeriods()
      .then((p) => {
        setPeriods(p);
        // 첫 번째와 다른 첫 기간을 train default로 (없으면 -1)
        if (p.length > 1) setTrainPeriodIdx(1);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // Train matrix fetch
  useEffect(() => {
    if (!periods || trainPeriodIdx < 0 || trainPeriodIdx >= periods.length) {
      setTrainMatrix(null);
      return;
    }
    const p = periods[trainPeriodIdx];
    fetchMatrix({
      period_start: p.period_start,
      period_end: p.period_end,
    })
      .then(setTrainMatrix)
      .catch(() => setTrainMatrix(null));
  }, [periods, trainPeriodIdx]);

  // Multi-horizon: 12M anchor 기준으로 가장 잘 맞는 3M / 1M 매트릭스를 자동 fetch
  useEffect(() => {
    if (!multiHorizon || !periods || periods.length === 0) {
      setH3Matrix(null);
      setH1Matrix(null);
      return;
    }
    const anchor = periods[periodIdx];
    const h3 = findHorizon(periods, anchor.period_end, 90, 30);
    const h1 = findHorizon(periods, anchor.period_end, 30, 10);
    if (h3) {
      fetchMatrix({ period_start: h3.period_start, period_end: h3.period_end })
        .then(setH3Matrix)
        .catch(() => setH3Matrix(null));
    } else {
      setH3Matrix(null);
    }
    if (h1) {
      fetchMatrix({ period_start: h1.period_start, period_end: h1.period_end })
        .then(setH1Matrix)
        .catch(() => setH1Matrix(null));
    } else {
      setH1Matrix(null);
    }
  }, [multiHorizon, periods, periodIdx]);

  // Load matrix when period changes
  useEffect(() => {
    if (!periods || periods.length === 0) {
      setLoading(false);
      return;
    }
    const period = periods[periodIdx];
    setLoading(true);
    Promise.all([
      fetchMatrix({
        period_start: period.period_start,
        period_end: period.period_end,
      }),
      fetchAssignments().catch(() => [] as Assignment[]),
    ])
      .then(([m, a]) => {
        setMatrix(m);
        setAssignments(a);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [periods, periodIdx]);

  // grid lookup — 같은 (symbol, preset_key) 셀이 여러 params_hash로 있을 수 있음.
  // /api/matrix가 fitness desc로 정렬해 보내므로 먼저 들어온(=fitness 높은) 셀을 우선.
  const grid = useMemo(() => {
    if (!matrix) return new Map<CellKey, MatrixCell>();
    const m = new Map<CellKey, MatrixCell>();
    matrix.cells.forEach((c) => {
      const k = cellKey(c.symbol, c.preset_key);
      if (!m.has(k)) m.set(k, c);
    });
    return m;
  }, [matrix]);

  // train fitness lookup
  const trainFitness = useMemo(() => {
    const m = new Map<CellKey, number>();
    trainMatrix?.cells.forEach((c) => {
      const k = cellKey(c.symbol, c.preset_key);
      if (!m.has(k)) m.set(k, c.fitness);
    });
    return m;
  }, [trainMatrix]);

  const h3Fitness = useMemo(() => {
    const m = new Map<CellKey, number>();
    h3Matrix?.cells.forEach((c) => {
      const k = cellKey(c.symbol, c.preset_key);
      if (!m.has(k)) m.set(k, c.fitness);
    });
    return m;
  }, [h3Matrix]);

  const h1Fitness = useMemo(() => {
    const m = new Map<CellKey, number>();
    h1Matrix?.cells.forEach((c) => {
      const k = cellKey(c.symbol, c.preset_key);
      if (!m.has(k)) m.set(k, c.fitness);
    });
    return m;
  }, [h1Matrix]);

  function trustOf(c: MatrixCell): TrustCategory {
    return classifyTrust(trainFitness.get(cellKey(c.symbol, c.preset_key)), c.fitness);
  }

  // 3-of-3 양호 판단: 12M / 3M / 1M 모두 fitness ≥ threshold
  function multiHorizonOk(c: MatrixCell, threshold = 0.3): boolean {
    if (c.fitness < threshold) return false;
    const k = cellKey(c.symbol, c.preset_key);
    const f3 = h3Fitness.get(k);
    const f1 = h1Fitness.get(k);
    if (f3 === undefined || f1 === undefined) return false;
    return f3 >= threshold && f1 >= threshold;
  }

  // 종목별 best preset — Trust mode면 OVERFIT 제외, multi-horizon이면 3-of-3 양호만 best 후보
  const bestBySymbol = useMemo(() => {
    const m = new Map<string, MatrixCell>();
    grid.forEach((c) => {
      if (trustMode && trustOf(c) === "OVERFIT") return;
      if (multiHorizon && !multiHorizonOk(c)) return;
      const cur = m.get(c.symbol);
      if (!cur || c.fitness > cur.fitness) m.set(c.symbol, c);
    });
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid, trainFitness, h3Fitness, h1Fitness, trustMode, multiHorizon]);

  const enabledKeys = useMemo(() => {
    const set = new Set<CellKey>();
    assignments?.forEach((a) => {
      if (a.enabled) set.add(cellKey(a.symbol, a.preset_key));
    });
    return set;
  }, [assignments]);

  const symbols = matrix?.symbols ?? [];
  const presets = matrix?.presets ?? [];

  async function handleToggle(cell: MatrixCell, currentlyEnabled: boolean) {
    try {
      await saveAssignment({
        symbol: cell.symbol,
        preset_key: cell.preset_key,
        enabled: !currentlyEnabled,
      });
      const refreshed = await fetchAssignments();
      setAssignments(refreshed);
    } catch (e) {
      alert(`Toggle failed: ${e}`);
    }
  }

  async function handleRunMatrix() {
    setRunning(true);
    try {
      const r = await triggerMatrixRun({
        pool: "default",
        presets: "all",
      });
      alert(`Started: ${r.message}`);
    } catch (e) {
      alert(`Run failed: ${e}`);
    } finally {
      setRunning(false);
    }
  }

  async function handleEnableAllBest() {
    if (!confirm(`Enable best preset for all ${bestBySymbol.size} symbols?`)) return;
    const cells = Array.from(bestBySymbol.values()).filter((c) => c.fitness > 0);
    let ok = 0;
    let fail = 0;
    for (const c of cells) {
      try {
        await saveAssignment({
          symbol: c.symbol,
          preset_key: c.preset_key,
          enabled: true,
          notes: `auto-best: fitness=${c.fitness.toFixed(2)}`,
        });
        ok++;
      } catch {
        fail++;
      }
    }
    alert(`Enabled ${ok} (skipped 0-fitness; ${fail} failed)`);
    const refreshed = await fetchAssignments();
    setAssignments(refreshed);
  }

  return (
    <main className="min-h-screen bg-zinc-50 p-6 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-[1600px] space-y-4">
        <TopNav />
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              Strategy×Symbol Fitness Matrix
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              어떤 종목에 어떤 거장 프리셋이 잘 맞는지. 셀 클릭 시 상세, 우측에서 활성화 토글.
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

        {regime && <RegimePanel regime={regime} />}

        <section className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
          <label className="text-sm">
            Test:&nbsp;
            <select
              value={periodIdx}
              onChange={(e) => setPeriodIdx(Number(e.target.value))}
              className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
            >
              {periods?.map((p, i) => (
                <option key={`${p.period_start}_${p.period_end}`} value={i}>
                  {p.period_start.slice(0, 10)} ~ {p.period_end.slice(0, 10)} ({p.count})
                </option>
              )) ?? <option>Loading…</option>}
            </select>
          </label>
          <label className="text-sm">
            Train (compare):&nbsp;
            <select
              value={trainPeriodIdx}
              onChange={(e) => setTrainPeriodIdx(Number(e.target.value))}
              className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
            >
              <option value={-1}>off</option>
              {periods?.map((p, i) =>
                i === periodIdx ? null : (
                  <option key={`t_${p.period_start}_${p.period_end}`} value={i}>
                    {p.period_start.slice(0, 10)} ~ {p.period_end.slice(0, 10)}
                  </option>
                ),
              )}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={trustMode}
              onChange={(e) => setTrustMode(e.target.checked)}
              disabled={trainPeriodIdx < 0}
            />
            <span className={trainPeriodIdx < 0 ? "text-zinc-400" : ""}>
              Trust mode (exclude OVERFIT)
            </span>
          </label>
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={multiHorizon}
              onChange={(e) => setMultiHorizon(e.target.checked)}
            />
            <span>Multi-horizon (12M / 3M / 1M)</span>
            {multiHorizon && (
              <span className="text-xs text-zinc-500">
                {h3Matrix ? `3M ${h3Matrix.period_start.slice(0, 10)}` : "3M —"} ·{" "}
                {h1Matrix ? `1M ${h1Matrix.period_start.slice(0, 10)}` : "1M —"}
              </span>
            )}
          </label>
          <button
            onClick={handleRunMatrix}
            disabled={running}
            className="rounded bg-zinc-900 px-3 py-1.5 text-sm text-white hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-black dark:hover:bg-zinc-300"
          >
            {running ? "Running…" : "Run matrix"}
          </button>
          <button
            onClick={handleEnableAllBest}
            disabled={bestBySymbol.size === 0}
            className="rounded border border-emerald-600 px-3 py-1.5 text-sm text-emerald-700 hover:bg-emerald-50 disabled:opacity-50 dark:text-emerald-400 dark:hover:bg-emerald-950"
          >
            ★ Enable best for all
          </button>
          <span className="text-xs text-zinc-500">
            {matrix && `${matrix.total} cells, ${symbols.length}×${presets.length}`}
            {trainMatrix && ` · train ${trainMatrix.total} cells`}
          </span>
        </section>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ {error}
          </div>
        )}

        {loading && !matrix ? (
          <p className="text-zinc-500">Loading…</p>
        ) : !matrix || matrix.total === 0 ? (
          <div className="rounded-lg border border-zinc-200 bg-white p-8 text-center text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900">
            <p>No matrix data yet.</p>
            <p className="mt-2 text-xs">
              CLI: <code className="rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800">python -m backtests.run_matrix --pool default --presets all</code>
            </p>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
            {/* Heatmap */}
            <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
              <table className="text-xs">
                <thead className="sticky top-0 bg-zinc-100 dark:bg-zinc-950">
                  <tr>
                    <th className="sticky left-0 z-10 bg-zinc-100 px-2 py-2 text-left dark:bg-zinc-950">
                      Symbol
                    </th>
                    {presets.map((p) => (
                      <th key={p} className="whitespace-nowrap px-2 py-2 text-center font-medium">
                        {p}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {symbols.map((sym) => {
                    const best = bestBySymbol.get(sym);
                    return (
                      <tr key={sym}>
                        <td className="sticky left-0 z-10 bg-white px-2 py-1 dark:bg-zinc-900">
                          <div className="font-mono">{sym}</div>
                          {best && best.fitness > 0 && (
                            <div className="whitespace-nowrap text-[10px] leading-tight text-emerald-700 dark:text-emerald-400">
                              ★ {best.preset_key} ({best.fitness.toFixed(2)})
                            </div>
                          )}
                        </td>
                        {presets.map((p) => {
                          const cell = grid.get(cellKey(sym, p));
                          const isEnabled = enabledKeys.has(cellKey(sym, p));
                          const isSelected =
                            selected?.symbol === sym && selected?.preset_key === p;
                          const isBest =
                            best && best.preset_key === p && best.fitness > 0;
                          if (!cell) {
                            return (
                              <td key={p} className="px-2 py-1 text-center text-zinc-400">
                                —
                              </td>
                            );
                          }
                          const trust = trainPeriodIdx >= 0 ? trustOf(cell) : "NO_DATA";
                          const dim =
                            (trustMode && trust === "OVERFIT") ||
                            (multiHorizon && !multiHorizonOk(cell));
                          const trainFit = trainFitness.get(cellKey(sym, p));
                          const f3 = h3Fitness.get(cellKey(sym, p));
                          const f1 = h1Fitness.get(cellKey(sym, p));
                          return (
                            <td
                              key={p}
                              onClick={() => setSelected(cell)}
                              className={`relative cursor-pointer px-2 py-1 text-center font-mono text-white transition ${
                                isSelected
                                  ? "ring-2 ring-blue-500"
                                  : isBest
                                    ? "outline outline-2 outline-amber-400"
                                    : ""
                              } ${dim ? "opacity-30 grayscale" : ""}`}
                              style={{ backgroundColor: fitnessColor(cell.fitness) }}
                              title={`12M fitness=${cell.fitness.toFixed(3)}${
                                f3 !== undefined ? ` · 3M=${f3.toFixed(3)}` : ""
                              }${f1 !== undefined ? ` · 1M=${f1.toFixed(3)}` : ""}${
                                trainFit !== undefined ? ` · train=${trainFit.toFixed(3)}` : ""
                              } · sharpe=${cell.sharpe.toFixed(2)} MDD=${(cell.max_drawdown * 100).toFixed(1)}% trades=${cell.total_positions}${isBest ? " [BEST]" : ""}${trust !== "NO_DATA" ? ` · ${trust}: ${TRUST_LABEL[trust]}` : ""}`}
                            >
                              <div>{cell.fitness.toFixed(2)}</div>
                              {multiHorizon && (
                                <div className="text-[9px] leading-tight opacity-90">
                                  {f3 !== undefined ? f3.toFixed(2) : "—"} /{" "}
                                  {f1 !== undefined ? f1.toFixed(2) : "—"}
                                </div>
                              )}
                              {isEnabled && (
                                <span className="absolute right-0.5 top-0 text-[10px] leading-none">
                                  ★
                                </span>
                              )}
                              {trust !== "NO_DATA" && trust !== "WEAK" && (
                                <span
                                  className={`absolute left-0.5 top-0.5 inline-block h-1.5 w-1.5 rounded-full ${TRUST_DOT[trust]}`}
                                />
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Detail panel */}
            <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              {!selected ? (
                <p className="text-sm text-zinc-500">셀을 클릭하면 상세가 표시됩니다.</p>
              ) : (
                <CellDetail
                  cell={selected}
                  enabled={enabledKeys.has(cellKey(selected.symbol, selected.preset_key))}
                  onToggle={(curr) => handleToggle(selected, curr)}
                  trainFitness={trainFitness.get(
                    cellKey(selected.symbol, selected.preset_key),
                  )}
                  trust={
                    trainPeriodIdx >= 0
                      ? classifyTrust(
                          trainFitness.get(cellKey(selected.symbol, selected.preset_key)),
                          selected.fitness,
                        )
                      : "NO_DATA"
                  }
                  fitness3m={h3Fitness.get(cellKey(selected.symbol, selected.preset_key))}
                  fitness1m={h1Fitness.get(cellKey(selected.symbol, selected.preset_key))}
                  multiHorizon={multiHorizon}
                />
              )}
            </div>
          </div>
        )}

        <Legend />
      </div>
    </main>
  );
}

function CellDetail({
  cell,
  enabled,
  onToggle,
  trainFitness,
  trust,
  fitness3m,
  fitness1m,
  multiHorizon,
}: {
  cell: MatrixCell;
  enabled: boolean;
  onToggle: (currentlyEnabled: boolean) => void;
  trainFitness: number | undefined;
  trust: TrustCategory;
  fitness3m: number | undefined;
  fitness1m: number | undefined;
  multiHorizon: boolean;
}) {
  const trustBadgeColor: Record<TrustCategory, string> = {
    ROBUST: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
    OVERFIT: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
    DECAYED: "bg-black text-white dark:bg-white dark:text-black",
    WEAK: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
    NO_DATA: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-500",
  };

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <div>
          <h3 className="text-lg font-semibold text-black dark:text-zinc-50">
            {cell.symbol} × {cell.preset_key}
          </h3>
          <p className="text-xs text-zinc-500">
            {cell.period_start.slice(0, 10)} ~ {cell.period_end.slice(0, 10)}
          </p>
        </div>
        <button
          onClick={() => onToggle(enabled)}
          className={`rounded px-3 py-1.5 text-sm font-medium ${
            enabled
              ? "bg-emerald-600 text-white hover:bg-emerald-700"
              : "border border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          }`}
        >
          {enabled ? "★ Enabled" : "Enable"}
        </button>
      </div>

      {trust !== "NO_DATA" && (
        <div className={`rounded-md px-3 py-2 text-xs ${trustBadgeColor[trust]}`}>
          <strong>{trust}</strong> — {TRUST_LABEL[trust]}
          {trainFitness !== undefined && (
            <span className="ml-2 font-mono">
              (train {trainFitness.toFixed(3)} / test {cell.fitness.toFixed(3)})
            </span>
          )}
        </div>
      )}

      {multiHorizon && (
        <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs dark:border-zinc-800 dark:bg-zinc-950">
          <div className="mb-1 font-semibold text-zinc-700 dark:text-zinc-300">
            Multi-horizon fitness
          </div>
          <div className="grid grid-cols-3 gap-2 font-mono">
            <div>
              <span className="text-zinc-500">12M:</span>{" "}
              <span className={cell.fitness >= 0.3 ? "text-emerald-600 dark:text-emerald-400" : "text-zinc-500"}>
                {cell.fitness.toFixed(2)}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">3M:</span>{" "}
              <span className={fitness3m !== undefined && fitness3m >= 0.3 ? "text-emerald-600 dark:text-emerald-400" : "text-zinc-500"}>
                {fitness3m !== undefined ? fitness3m.toFixed(2) : "—"}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">1M:</span>{" "}
              <span className={fitness1m !== undefined && fitness1m >= 0.3 ? "text-emerald-600 dark:text-emerald-400" : "text-zinc-500"}>
                {fitness1m !== undefined ? fitness1m.toFixed(2) : "—"}
              </span>
            </div>
          </div>
          <div className="mt-1 text-[11px] text-zinc-500">
            셋 다 ≥ 0.3이면 best 후보로 채택. 표본 부족(거래 0~1)은 — 표시.
          </div>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-2 text-sm">
        <Stat label="Fitness" value={cell.fitness.toFixed(3)} highlight />
        <Stat label="Sharpe" value={cell.sharpe.toFixed(2)} />
        <Stat
          label="Max drawdown"
          value={`${(cell.max_drawdown * 100).toFixed(2)}%`}
        />
        <Stat
          label="Total return"
          value={`${(cell.total_return * 100).toFixed(2)}%`}
          color={cell.total_return > 0 ? "emerald" : cell.total_return < 0 ? "red" : "neutral"}
        />
        <Stat
          label="Cost-adj return"
          value={`${(cell.cost_adj_return * 100).toFixed(2)}%`}
          color={cell.cost_adj_return > 0 ? "emerald" : cell.cost_adj_return < 0 ? "red" : "neutral"}
        />
        <Stat label="Win rate" value={`${(cell.win_rate * 100).toFixed(1)}%`} />
        <Stat label="Trades" value={String(cell.total_positions)} />
        <Stat label="Fills" value={String(cell.total_fills)} />
        <Stat label="Wins / Losses" value={`${cell.wins} / ${cell.losses}`} />
        <Stat
          label="Final equity"
          value={`$${cell.final_equity.toFixed(2)}`}
        />
      </dl>

      <div className="border-t border-zinc-200 pt-2 text-xs text-zinc-500 dark:border-zinc-800">
        <code>{cell.params_hash}</code>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color = "neutral",
  highlight = false,
}: {
  label: string;
  value: string;
  color?: "emerald" | "red" | "neutral";
  highlight?: boolean;
}) {
  const colorClass =
    color === "emerald"
      ? "text-emerald-600 dark:text-emerald-400"
      : color === "red"
        ? "text-red-600 dark:text-red-400"
        : "text-black dark:text-zinc-50";
  return (
    <div className={highlight ? "rounded bg-zinc-100 p-2 dark:bg-zinc-800" : ""}>
      <dt className="text-xs text-zinc-500">{label}</dt>
      <dd className={`font-mono ${colorClass}`}>{value}</dd>
    </div>
  );
}

const REGIME_BADGE: Record<string, { color: string; label: string }> = {
  RANGING: { color: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300", label: "RANGING — 횡보장" },
  TRENDING: { color: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300", label: "TRENDING — 추세장" },
  MIXED: { color: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300", label: "MIXED — 혼재" },
  TRANSITION_TO_TRENDING: { color: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300", label: "TRANSITION → TRENDING" },
  TRANSITION_TO_RANGING: { color: "bg-cyan-100 text-cyan-800 dark:bg-cyan-950 dark:text-cyan-300", label: "TRANSITION → RANGING" },
  WEAK_MARKET: { color: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300", label: "WEAK_MARKET — 약체장" },
  UNKNOWN: { color: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400", label: "UNKNOWN" },
};

const ALERT_SEVERITY: Record<string, string> = {
  info: "border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300",
  warning: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  critical: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300",
};

function RegimePanel({ regime }: { regime: RegimeResponse }) {
  const badge = REGIME_BADGE[regime.regime] ?? REGIME_BADGE.UNKNOWN;
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`rounded px-2 py-0.5 text-xs font-bold ${badge.color}`}>
              {badge.label}
            </span>
            <span className="text-xs text-zinc-500">{regime.description}</span>
          </div>
          <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
            <strong>권장:</strong> {regime.recommendation}
          </p>
        </div>
      </div>

      {regime.alerts.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {regime.alerts.map((a, i) => (
            <div
              key={i}
              className={`rounded-md border px-3 py-1.5 text-xs ${ALERT_SEVERITY[a.severity] ?? ALERT_SEVERITY.info}`}
            >
              <strong>[{a.code}]</strong> {a.message}
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
        {regime.snapshots.map((s) => (
          <div
            key={s.label}
            className="rounded border border-zinc-200 p-2 dark:border-zinc-800"
          >
            <div className="mb-1 flex items-baseline justify-between">
              <span className="font-semibold">{s.label}</span>
              <span className="text-[10px] text-zinc-500">
                {s.period_start.slice(0, 10)} ~ {s.period_end.slice(0, 10)}
              </span>
            </div>
            <div className="space-y-0.5">
              {s.presets.map((p) => {
                const high = p.hit_rate >= 0.7;
                const low = p.hit_rate < 0.3;
                return (
                  <div key={p.preset_key} className="flex items-baseline gap-2">
                    <span className="w-28 truncate font-mono">{p.preset_key}</span>
                    <span
                      className={`flex-1 font-mono ${
                        high
                          ? "text-emerald-600 dark:text-emerald-400"
                          : low
                            ? "text-red-600 dark:text-red-400"
                            : "text-zinc-700 dark:text-zinc-300"
                      }`}
                    >
                      {(p.hit_rate * 100).toFixed(0)}%
                    </span>
                    <span className="font-mono text-[10px] text-zinc-500">
                      avg {p.avg_fitness >= 0 ? "+" : ""}{p.avg_fitness.toFixed(2)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Legend() {
  const stops = [-0.5, 0, 0.5, 1.0, 1.5];
  return (
    <div className="flex items-center gap-3 text-xs text-zinc-500">
      <span>Fitness:</span>
      {stops.map((f) => (
        <span key={f} className="flex items-center gap-1">
          <span
            className="inline-block h-3 w-6 rounded"
            style={{ backgroundColor: fitnessColor(f) }}
          />
          {f.toFixed(1)}
        </span>
      ))}
      <span className="ml-4 inline-flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded-sm outline outline-2 outline-amber-400" />
        best per symbol
      </span>
      <span className="ml-3 inline-flex items-center gap-1">
        <span className="inline-block h-2 w-2 rounded-full bg-blue-500" />
        ROBUST
      </span>
      <span className="ml-1.5 inline-flex items-center gap-1">
        <span className="inline-block h-2 w-2 rounded-full bg-red-500" />
        OVERFIT
      </span>
      <span className="ml-1.5 inline-flex items-center gap-1">
        <span className="inline-block h-2 w-2 rounded-full bg-black dark:bg-white" />
        DECAYED
      </span>
      <span className="ml-3 inline-flex items-center gap-1 text-zinc-600 dark:text-zinc-400">
        Multi-horizon: 셀 큰 숫자=12M, 작은 숫자=3M / 1M (3-of-3 ≥ 0.3 → best 후보)
      </span>
      <span className="ml-auto">★ = enabled</span>
    </div>
  );
}
