"use client";

import { useEffect, useMemo, useState } from "react";

import TopNav from "@/components/TopNav";
import MarketDiagnosisCard from "@/components/MarketDiagnosisCard";
import {
  deleteTradePlan,
  fetchTradingToday,
  saveTradePlan,
  type MarketBrief,
  type PickRecommendation,
  type ScoreBreakdownItem,
  type TradePlan,
  type TradingTodayResponse,
} from "@/lib/api";
import HelpDrawer from "./HelpDrawer";

const REGIME_LABEL: Record<string, { ko: string; cls: string; emoji: string }> = {
  aggressive: {
    ko: "공격모드",
    cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    emoji: "🟢",
  },
  neutral: {
    ko: "중립",
    cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
    emoji: "🟡",
  },
  defensive: {
    ko: "방어모드",
    cls: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
    emoji: "🛑",
  },
};

type InputMode = "amount" | "shares";
type SymInput = { mode: InputMode; value: string };

export default function TradingPage() {
  const [data, setData] = useState<TradingTodayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  // 종목별 입력 — 사용자가 마지막 편집한 쪽 (amount 또는 shares)
  const [inputs, setInputs] = useState<Record<string, SymInput>>({});
  const [savingSymbol, setSavingSymbol] = useState<string | null>(null);
  // 저장 직후 토스트 — 사용자 시각 피드백
  const [toast, setToast] = useState<string | null>(null);
  // 저장 직후 카드 자체 강조 표시 (symbol → ms timestamp)
  const [justSavedSymbol, setJustSavedSymbol] = useState<string | null>(null);

  function load() {
    fetchTradingToday()
      .then((d) => {
        setData(d);
        // 기존 plan 있으면 입력란에 amount 모드로 미리 채우기
        const initial: Record<string, SymInput> = {};
        d.existing_plans.forEach((ep) => {
          initial[ep.symbol] = { mode: "amount", value: ep.amount_usd };
        });
        setInputs((prev) => ({ ...initial, ...prev }));
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    load();
  }, []);

  // ESC로 도움말 닫기
  useEffect(() => {
    if (!helpOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHelpOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [helpOpen]);

  async function onSave(
    pick: PickRecommendation,
    overrides?: { entry_price: number; stop_price: number; target_1r: number; target_2r: number },
  ) {
    const inp = inputs[pick.symbol];
    if (!inp) {
      alert("금액 또는 수량을 입력하세요.");
      return;
    }
    const num = parseFloat(inp.value);
    if (!Number.isFinite(num) || num <= 0) {
      alert("유효한 값을 입력하세요.");
      return;
    }
    const effEntry = overrides?.entry_price ?? parseFloat(pick.entry_price);
    const effStop = overrides?.stop_price ?? parseFloat(pick.stop_price);
    const effT1 = overrides?.target_1r ?? parseFloat(pick.target_1r);
    const effT2 = overrides?.target_2r ?? parseFloat(pick.target_2r);
    const payload = {
      symbol: pick.symbol,
      rank: pick.rank,
      entry_price: effEntry,
      stop_price: effStop,
      target_1r: effT1,
      target_2r: effT2,
      composite_score: pick.composite_score,
      sector: pick.sector,
      score_meta: pick.score_meta,
      ...(inp.mode === "amount"
        ? { amount_usd: num }
        : { shares: Math.floor(num) }),
    };
    setSavingSymbol(pick.symbol);
    try {
      await saveTradePlan(payload);
      const editedNote = overrides && Math.abs(overrides.entry_price - parseFloat(pick.entry_price)) > 0.005
        ? ` (진입가 $${overrides.entry_price.toFixed(2)})`
        : "";
      setToast(`✓ ${pick.symbol} 저장됨${editedNote}`);
      window.setTimeout(() => setToast(null), 4000);
      setJustSavedSymbol(pick.symbol);
      window.setTimeout(() => setJustSavedSymbol((cur) => (cur === pick.symbol ? null : cur)), 2500);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingSymbol(null);
    }
  }

  async function onDelete(plan: TradePlan) {
    if (!confirm(`${plan.symbol} 매매 plan을 삭제하시겠습니까?`)) return;
    try {
      await deleteTradePlan(plan.id);
      setInputs((prev) => {
        const next = { ...prev };
        delete next[plan.symbol];
        return next;
      });
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  // 합계 계산 (입력 + 저장된 plan 통합)
  const totals = useMemo(() => {
    if (!data) return { exposure: 0, risk: 0 };
    let exposure = 0;
    let risk = 0;
    // 저장된 plan 우선
    const savedSymbols = new Set<string>();
    data.existing_plans.forEach((ep) => {
      savedSymbols.add(ep.symbol);
      exposure += parseFloat(ep.amount_usd);
      risk += parseFloat(ep.risk_usd);
    });
    // 미저장 입력
    data.picks.forEach((p) => {
      if (savedSymbols.has(p.symbol)) return;
      const inp = inputs[p.symbol];
      if (!inp) return;
      const num = parseFloat(inp.value);
      if (!Number.isFinite(num) || num <= 0) return;
      const entry = parseFloat(p.entry_price);
      const stop = parseFloat(p.stop_price);
      const shares =
        inp.mode === "amount" ? Math.floor(num / entry) : Math.floor(num);
      const amt = inp.mode === "amount" ? num : shares * entry;
      exposure += amt;
      risk += shares * Math.max(0, entry - stop);
    });
    return { exposure, risk };
  }, [data, inputs]);

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      {toast && (
        <div
          className="pointer-events-none fixed left-1/2 top-4 z-[100] -translate-x-1/2 rounded-lg border-2 border-emerald-500 bg-emerald-100 px-6 py-3 text-base font-semibold text-emerald-900 shadow-2xl dark:border-emerald-400 dark:bg-emerald-900 dark:text-emerald-50"
          style={{ minWidth: "280px", textAlign: "center" }}
        >
          {toast}
        </div>
      )}
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-6xl space-y-5">
        <TopNav />
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              🎯 오늘의 매매 Plan
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              5-Model 단타 스택 — 시장 분석 + Top 5 워치리스트 → 개장 후 15분 확인(ORB+VWAP+상대거래량) → 통과 3종목 자동 발송
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
          </nav>
        </header>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ {error}
          </div>
        )}

        {toast && (
          <div className="rounded-lg border-2 border-emerald-500 bg-emerald-100 p-3 text-sm font-semibold text-emerald-900 dark:border-emerald-400 dark:bg-emerald-900 dark:text-emerald-50">
            {toast}
          </div>
        )}

        {data === null ? (
          <p className="text-zinc-500">불러오는 중…</p>
        ) : (
          <>
            <MarketDiagnosisCard variant="banner" />

            <BriefCard brief={data.market_brief} />

            <PicksSection
              title="⚡ 단타 (Intraday)"
              description="장 초반 ORB·VWAP·상대거래량 기준 — confirm 통과 시 자동 발송"
              emptyText="오늘 단타 조건을 충족하는 종목이 없습니다 (regime 차단 또는 게이트 미충족)."
              statusLegend
              picks={data.picks.filter((p) => p.system_source === "intraday_v1")}
              existingPlans={data.existing_plans}
              inputs={inputs}
              setInputs={setInputs}
              onSave={onSave}
              onDelete={onDelete}
              savingSymbol={savingSymbol}
              justSavedSymbol={justSavedSymbol}
            />

            <PicksSection
              title="📈 스윙 (Swing)"
              description="통합 v10 — 압축·팽창 셋업 기반 다일 보유"
              emptyText="오늘 스윙 조건을 충족하는 종목이 없습니다."
              statusLegend={false}
              picks={data.picks.filter((p) => p.system_source !== "intraday_v1")}
              existingPlans={data.existing_plans}
              inputs={inputs}
              setInputs={setInputs}
              onSave={onSave}
              onDelete={onDelete}
              savingSymbol={savingSymbol}
              justSavedSymbol={justSavedSymbol}
            />

            <SummaryCard totals={totals} />
          </>
        )}
      </div>
    </main>
  );
}

function PicksSection({
  title,
  description,
  emptyText,
  statusLegend,
  picks,
  existingPlans,
  inputs,
  setInputs,
  onSave,
  onDelete,
  savingSymbol,
  justSavedSymbol,
}: {
  title: string;
  description: string;
  emptyText: string;
  statusLegend: boolean;
  picks: PickRecommendation[];
  existingPlans: TradePlan[];
  inputs: Record<string, SymInput>;
  setInputs: React.Dispatch<React.SetStateAction<Record<string, SymInput>>>;
  onSave: (
    pick: PickRecommendation,
    overrides?: { entry_price: number; stop_price: number; target_1r: number; target_2r: number },
  ) => void;
  onDelete: (plan: TradePlan) => void;
  savingSymbol: string | null;
  justSavedSymbol: string | null;
}) {
  return (
    <section>
      <h2 className="mb-1 text-xl font-semibold text-black dark:text-zinc-50">
        {title}{" "}
        <span className="text-sm font-normal text-zinc-500">({picks.length})</span>
      </h2>
      <p className="mb-3 text-xs text-zinc-500 dark:text-zinc-400">{description}</p>
      {statusLegend && (
        <p className="mb-3 text-xs text-zinc-500 dark:text-zinc-400">
          상태:
          <span className="ml-2 inline-flex items-center gap-1 rounded bg-zinc-100 px-2 py-0.5 dark:bg-zinc-800">⏳ watchlist</span>
          <span className="ml-1 inline-flex items-center gap-1 rounded bg-emerald-100 px-2 py-0.5 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">✅ passed</span>
          <span className="ml-1 inline-flex items-center gap-1 rounded bg-red-100 px-2 py-0.5 text-red-700 dark:bg-red-950 dark:text-red-300">❌ failed</span>
          <span className="ml-1 inline-flex items-center gap-1 rounded bg-blue-100 px-2 py-0.5 text-blue-700 dark:bg-blue-950 dark:text-blue-300">📤 sent</span>
        </p>
      )}
      {picks.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-6 text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/50">
          {emptyText}
        </div>
      ) : (
        <div className="grid gap-3 lg:grid-cols-3">
          {picks.map((p) => {
            const saved = existingPlans.find((ep) => ep.symbol === p.symbol);
            return (
              <PickCard
                key={p.symbol}
                pick={p}
                input={inputs[p.symbol]}
                saved={saved}
                onInputChange={(next) =>
                  setInputs((prev) => ({ ...prev, [p.symbol]: next }))
                }
                onSave={(overrides) => onSave(p, overrides)}
                onDelete={saved ? () => onDelete(saved) : undefined}
                saving={savingSymbol === p.symbol}
                justSaved={justSavedSymbol === p.symbol}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}


function BriefCard({ brief }: { brief: MarketBrief }) {
  const meta = REGIME_LABEL[brief.regime_mode] ?? REGIME_LABEL.neutral;
  const indices = Object.entries(brief.indices);
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-center gap-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          시장 분위기
        </span>
        <span className={`rounded px-2 py-0.5 text-sm font-bold ${meta.cls}`}>
          {meta.emoji} {meta.ko}
        </span>
        <span className="text-sm font-mono text-zinc-700 dark:text-zinc-300">
          {brief.regime_score.toFixed(1)}/15
        </span>
        <span className="text-xs text-zinc-500">
          포지션 사이즈 ×{brief.position_size_multiplier.toFixed(1)}
        </span>
      </div>
      {indices.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2 text-xs">
          {indices.map(([name, val]) => (
            <span
              key={name}
              className="rounded bg-zinc-100 px-2 py-0.5 font-mono dark:bg-zinc-800"
            >
              {name}{" "}
              <span className={val >= 0 ? "text-emerald-600" : "text-red-600"}>
                {val >= 0 ? "+" : ""}
                {val.toFixed(2)}%
              </span>
            </span>
          ))}
        </div>
      )}
      <p className="text-sm text-zinc-700 dark:text-zinc-300">{brief.summary}</p>
    </section>
  );
}

function PickCard({
  pick,
  input,
  saved,
  onInputChange,
  onSave,
  onDelete,
  saving,
  justSaved,
}: {
  pick: PickRecommendation;
  input?: SymInput;
  saved?: TradePlan;
  onInputChange: (next: SymInput) => void;
  onSave: (overrides: { entry_price: number; stop_price: number; target_1r: number; target_2r: number }) => void;
  onDelete?: () => void;
  saving: boolean;
  justSaved: boolean;
}) {
  const systemEntry = parseFloat(pick.entry_price);
  const stop = parseFloat(pick.stop_price);
  // saved plan이 있으면 그 entry, 아니면 시스템 추천 entry로 시작 (NaN 방어)
  const [entryStr, setEntryStr] = useState<string>(() => {
    const v = saved ? parseFloat(saved.entry_price) : systemEntry;
    return Number.isFinite(v) && v > 0 ? v.toFixed(2) : "";
  });

  // saved 또는 pick.entry_price가 바뀌면 input 동기화
  useEffect(() => {
    const v = saved ? parseFloat(saved.entry_price) : parseFloat(pick.entry_price);
    if (Number.isFinite(v) && v > 0) setEntryStr(v.toFixed(2));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved?.entry_price, pick.entry_price]);

  const parsedEntry = parseFloat(entryStr);
  const entryValid = Number.isFinite(parsedEntry) && Number.isFinite(stop) && parsedEntry > stop;
  const entry = entryValid ? parsedEntry : (Number.isFinite(systemEntry) ? systemEntry : 0);
  // entry 기준으로 1R/2R 재계산. stop은 기술적 레벨이라 시스템 값 유지.
  const oneR = entry - stop;
  const t1 = entry + oneR;
  const t2 = entry + oneR * 2;
  const riskPct = oneR > 0 ? (oneR / entry) * 100 : 0;
  const entryEdited = Math.abs(entry - systemEntry) > 0.005;

  const inp = input;
  const num = inp ? parseFloat(inp.value) : NaN;
  const validInput = Number.isFinite(num) && num > 0;

  let shares = 0;
  let amount = 0;
  if (validInput && inp) {
    if (inp.mode === "amount") {
      amount = num;
      shares = Math.floor(num / entry);
    } else {
      shares = Math.floor(num);
      amount = shares * entry;
    }
  }
  const riskUsd = shares * Math.max(0, oneR);
  const upside1R = shares * Math.max(0, t1 - entry);
  const upside2R = shares * Math.max(0, t2 - entry);
  const amountValue = inp?.mode === "amount" ? inp.value : amount > 0 ? amount.toFixed(2) : "";
  const sharesValue = inp?.mode === "shares" ? inp.value : shares > 0 ? String(shares) : "";

  const meta = pick.score_meta || {};
  const tier = meta.tier as number | undefined;
  const goldenSetup = !!meta.golden_setup;
  const peadBonus = (meta.pead_bonus as number | undefined) ?? 0;
  const source = meta.source as string | undefined;
  const intradayMeta = (meta.version === "intraday_v1");
  const confirmStatus = saved?.confirm_status ?? "watchlist";
  const orbEval = (saved?.score_meta as { orb_evaluation?: Record<string, unknown> } | undefined)?.orb_evaluation;

  return (
    <div
      className={`rounded-lg border-2 bg-white p-4 transition-all dark:bg-zinc-900 ${
        justSaved
          ? "border-emerald-500 ring-2 ring-emerald-300 dark:border-emerald-400 dark:ring-emerald-700"
          : saved
          ? "border-emerald-400 dark:border-emerald-700"
          : "border-blue-300 dark:border-blue-900"
      }`}
    >
      <div className="flex items-baseline justify-between">
        <div>
          <span className="text-xs text-zinc-500">#{pick.rank}</span>{" "}
          <span className="text-2xl font-bold text-black dark:text-zinc-50">
            {pick.symbol}
          </span>
          {tier && (
            <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] dark:bg-zinc-800">
              Tier {tier}
            </span>
          )}
          <ConsensusBadge tier={pick.consensus_tier} />
          {source === "v9_fallback" && (
            <span
              className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800 dark:bg-amber-950 dark:text-amber-300"
              title="v10이 quality gate로 부족분 → v9 (auto-blacklist 미적용) 보충"
            >
              v9 보충
            </span>
          )}
        </div>
        <span className="text-lg font-mono text-blue-600 dark:text-blue-400">
          {pick.composite_score.toFixed(1)}
        </span>
      </div>
      {pick.sector && (
        <p className="text-xs text-zinc-500">{pick.sector}</p>
      )}

      <div className="mt-2 flex flex-wrap gap-1">
        {goldenSetup && (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] dark:bg-amber-950 dark:text-amber-300">
            🌟 Golden Setup
          </span>
        )}
        {peadBonus > 0 && (
          <span className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] dark:bg-purple-950 dark:text-purple-300">
            📈 PEAD
          </span>
        )}
        {(meta.stage2_pass as boolean) && (
          <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] dark:bg-blue-950 dark:text-blue-300">
            Stage 2
          </span>
        )}
        {intradayMeta && <ConfirmStatusBadge status={confirmStatus} />}
      </div>

      {intradayMeta && saved && (
        <IntradayMetricsRow plan={saved} orbEval={orbEval} pickMeta={meta} />
      )}

      <SystemMatchRow systems={pick.consensus_systems} />

      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-zinc-500" title={entryEdited ? `시스템 추천: $${systemEntry.toFixed(2)}` : undefined}>
          진입가 {entryEdited && <span className="text-blue-600 dark:text-blue-400">●</span>}
        </dt>
        <dd className="text-right">
          <div className={`inline-flex items-center rounded border px-1 ${entryEdited ? "border-blue-400 dark:border-blue-700" : "border-zinc-200 dark:border-zinc-700"}`}>
            <span className="text-zinc-500">$</span>
            <input
              type="number"
              step="0.01"
              min="0"
              value={entryStr}
              onChange={(e) => setEntryStr(e.target.value)}
              className="w-20 bg-transparent py-0.5 text-right font-mono text-xs outline-none"
              title="진입가 수정 — 1R/2R/주식수/위험액 자동 재계산"
            />
          </div>
        </dd>
        <Row
          label="손절가"
          value={`$${stop.toFixed(2)}`}
          tone="negative"
          tooltip={`-${riskPct.toFixed(2)}%`}
        />
        <Row label="1R 목표" value={`$${t1.toFixed(2)}`} tone="positive" />
        <Row label="2R 목표" value={`$${t2.toFixed(2)}`} tone="positive" />
        <Row label="1R 단가" value={`$${oneR.toFixed(2)}`} />
        <Row label="위험률" value={`${riskPct.toFixed(2)}%`} tone="negative" />
      </dl>
      {!entryValid && (
        <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
          ⚠ 진입가는 손절가 ${stop.toFixed(2)}보다 커야 합니다.
        </p>
      )}

      <ScoreBreakdown items={pick.score_breakdown} composite={pick.composite_score} />

      <div className="mt-4 rounded bg-zinc-50 p-3 dark:bg-zinc-950">
        <p className="mb-1 text-[11px] text-zinc-500">
          금액 또는 수량 중 한쪽만 입력 — 다른 쪽은 자동 계산
        </p>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
          <div className="min-w-0">
            <label className="flex items-center justify-between text-xs text-zinc-500">
              <span>투자 금액 (USD)</span>
              {inp?.mode === "amount" && (
                <span className="text-[10px] text-blue-600 dark:text-blue-400">
                  ●
                </span>
              )}
            </label>
            <div
              className={`mt-1 flex items-center rounded border px-2 ${
                inp?.mode === "amount"
                  ? "border-blue-400 bg-white dark:border-blue-700 dark:bg-zinc-900"
                  : "border-zinc-300 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900"
              }`}
            >
              <span className="pr-1 text-zinc-500">$</span>
              <input
                type="number"
                min="0"
                step="100"
                value={amountValue}
                onChange={(e) =>
                  onInputChange({ mode: "amount", value: e.target.value })
                }
                placeholder="2000"
                className={`w-full min-w-0 bg-transparent py-1 text-right font-mono text-sm outline-none ${
                  inp?.mode === "amount"
                    ? ""
                    : "text-zinc-500 dark:text-zinc-500"
                }`}
              />
            </div>
          </div>
          <div className="min-w-0">
            <label className="flex items-center justify-between text-xs text-zinc-500">
              <span>수량 (주)</span>
              {inp?.mode === "shares" && (
                <span className="text-[10px] text-blue-600 dark:text-blue-400">
                  ●
                </span>
              )}
            </label>
            <div
              className={`mt-1 flex items-center rounded border px-2 ${
                inp?.mode === "shares"
                  ? "border-blue-400 bg-white dark:border-blue-700 dark:bg-zinc-900"
                  : "border-zinc-300 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900"
              }`}
            >
              <input
                type="number"
                min="0"
                step="1"
                value={sharesValue}
                onChange={(e) =>
                  onInputChange({ mode: "shares", value: e.target.value })
                }
                placeholder="5"
                className={`w-full min-w-0 bg-transparent py-1 text-right font-mono text-sm outline-none ${
                  inp?.mode === "shares"
                    ? ""
                    : "text-zinc-500 dark:text-zinc-500"
                }`}
              />
              <span className="pl-1 text-zinc-500">주</span>
            </div>
          </div>
        </div>
        {validInput && shares > 0 && (
          <div className="mt-2 space-y-0.5 text-xs">
            <div className="flex justify-between">
              <span className="text-zinc-500">실 매수 금액</span>
              <span className="font-mono font-semibold">
                ${(shares * entry).toFixed(2)}{" "}
                <span className="text-[10px] text-zinc-500">({shares}주 × ${entry.toFixed(2)})</span>
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">손실 한도</span>
              <span className="font-mono text-red-600 dark:text-red-400">
                -${riskUsd.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">1R 도달 시 수익</span>
              <span className="font-mono text-emerald-600 dark:text-emerald-400">
                +${upside1R.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">2R 도달 시 수익</span>
              <span className="font-mono text-emerald-600 dark:text-emerald-400">
                +${upside2R.toFixed(2)}
              </span>
            </div>
          </div>
        )}
        {validInput && shares === 0 && (
          <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
            ⚠ {inp?.mode === "amount"
              ? `금액이 진입가($${entry.toFixed(2)})보다 작아 0주 — 더 큰 금액 입력`
              : "유효한 수량을 입력 (≥1)"}
          </p>
        )}

        <div className="mt-3 flex gap-2">
          <button
            onClick={() => onSave({ entry_price: entry, stop_price: stop, target_1r: t1, target_2r: t2 })}
            disabled={saving || !validInput || shares === 0 || !entryValid}
            className={`flex-1 rounded px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 ${
              justSaved
                ? "bg-emerald-600 hover:bg-emerald-700"
                : "bg-blue-600 hover:bg-blue-700"
            }`}
          >
            {saving ? "저장 중…" : justSaved ? "✓ 방금 저장됨" : saved ? "✓ 저장됨 (갱신)" : "저장"}
          </button>
          {saved && onDelete && (
            <button
              onClick={onDelete}
              className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
            >
              취소
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  tone,
  tooltip,
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative";
  tooltip?: string;
}) {
  const color =
    tone === "positive"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "negative"
      ? "text-red-600 dark:text-red-400"
      : "text-black dark:text-zinc-50";
  return (
    <>
      <dt className="text-zinc-500" title={tooltip}>
        {label}
      </dt>
      <dd className={`text-right font-mono ${color}`}>{value}</dd>
    </>
  );
}

function ConsensusBadge({ tier }: { tier: "S" | "A" | "B" }) {
  const cfg = {
    S: {
      label: "🟢 S 합의",
      cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
      title: "v3 Picks + 스캐너 + 통합 모두 추천 — 가장 강한 합의",
    },
    A: {
      label: "🟡 A 부분합의",
      cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
      title: "v3 Picks 또는 스캐너 중 1개 + 통합 추천",
    },
    B: {
      label: "🔵 B 단독",
      cls: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
      title: "통합 시스템에서만 추천 — v3·스캐너에는 없음. 단일 시스템 신호이므로 추가 검토 권장",
    },
  }[tier];
  return (
    <span
      className={`ml-1 rounded px-1.5 py-0.5 text-[10px] font-semibold ${cfg.cls}`}
      title={cfg.title}
    >
      {cfg.label}
    </span>
  );
}

function SystemMatchRow({ systems }: { systems: string[] }) {
  const labels: Record<string, string> = {
    v3: "Picks",
    scanner: "스캐너",
    integrated: "통합",
  };
  return (
    <div className="mt-2 flex items-center gap-2 text-[11px]">
      <span className="text-zinc-500">시스템 매칭:</span>
      {(["v3", "scanner", "integrated"] as const).map((s) => {
        const matched = systems.includes(s);
        return (
          <span
            key={s}
            className={
              matched
                ? "font-semibold text-emerald-600 dark:text-emerald-400"
                : "text-zinc-400 line-through dark:text-zinc-600"
            }
            title={matched ? `${labels[s]}에서도 추천` : `${labels[s]}에는 없음`}
          >
            {matched ? "✓" : "✗"} {labels[s]}
          </span>
        );
      })}
    </div>
  );
}

function ScoreBreakdown({
  items,
  composite,
}: {
  items: ScoreBreakdownItem[];
  composite: number;
}) {
  if (!items || items.length === 0) return null;
  return (
    <details className="mt-3 rounded border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs dark:border-zinc-800 dark:bg-zinc-950">
      <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">
        📊 점수 근거 ({composite.toFixed(1)}점) — 상위 {items.length}개 항목
      </summary>
      <ul className="mt-2 space-y-0.5">
        {items.map((b) => {
          const isMult = b.kind === "multiplier";
          const positive = b.points >= 0;
          const display = isMult
            ? `×${(1 + b.points / 100).toFixed(2)}`
            : `${positive ? "+" : ""}${b.points.toFixed(1)}점`;
          return (
            <li key={b.name} className="flex justify-between font-mono">
              <span className="text-zinc-700 dark:text-zinc-300">
                {b.label_ko}
              </span>
              <span
                className={
                  positive
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-red-600 dark:text-red-400"
                }
              >
                {display}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-[10px] text-zinc-500">
        base = 5블록 기본 점수, bonus = 가산 항목, multiplier = 합의 배수
      </p>
    </details>
  );
}

function SummaryCard({ totals }: { totals: { exposure: number; risk: number } }) {
  if (totals.exposure === 0) return null;
  return (
    <section className="sticky bottom-4 rounded-lg border-2 border-blue-300 bg-white p-4 shadow-lg dark:border-blue-900 dark:bg-zinc-900">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 text-sm">
        <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          오늘 매매 합계
        </span>
        <div>
          <span className="text-zinc-500">총 노출 </span>
          <span className="font-mono text-lg font-bold text-blue-600 dark:text-blue-400">
            ${totals.exposure.toFixed(2)}
          </span>
        </div>
        <div>
          <span className="text-zinc-500">총 위험 </span>
          <span className="font-mono text-lg font-bold text-red-600 dark:text-red-400">
            -${totals.risk.toFixed(2)}
          </span>
        </div>
        <div className="text-xs text-zinc-500">
          (실제 매매는 Webull에서 직접 — 시스템은 계획·추적 advisory)
        </div>
      </div>
    </section>
  );
}


function ConfirmStatusBadge({ status }: { status: string }) {
  const cfg: Record<string, { ko: string; cls: string; emoji: string }> = {
    watchlist: { ko: "워치리스트", cls: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300", emoji: "⏳" },
    passed: { ko: "확인 통과", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300", emoji: "✅" },
    failed: { ko: "확인 실패", cls: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300", emoji: "❌" },
    sent: { ko: "주문 발송", cls: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300", emoji: "📤" },
    skipped: { ko: "건너뜀", cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300", emoji: "↪" },
  };
  const c = cfg[status] ?? cfg.watchlist;
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] ${c.cls}`}
      title={`확인 상태: ${status}`}
    >
      {c.emoji} {c.ko}
    </span>
  );
}


function IntradayMetricsRow({
  plan,
  orbEval,
  pickMeta,
}: {
  plan: TradePlan;
  orbEval?: Record<string, unknown>;
  pickMeta: Record<string, unknown>;
}) {
  const gap = plan.premarket_gap_pct ? parseFloat(plan.premarket_gap_pct) : null;
  const pmRvol = plan.premarket_rvol ? parseFloat(plan.premarket_rvol) : null;
  const orbHigh = plan.orb_high ? parseFloat(plan.orb_high) : null;
  const orbLow = plan.orb_low ? parseFloat(plan.orb_low) : null;
  const vwap = plan.session_vwap ? parseFloat(plan.session_vwap) : null;
  const intraRvol = plan.intraday_rvol ? parseFloat(plan.intraday_rvol) : null;
  const failReasons = (plan.score_meta as { confirm_fail_reasons?: string[] } | undefined)?.confirm_fail_reasons;
  const catalystSummary = pickMeta.catalyst_summary as string | undefined;

  return (
    <div className="mt-2 rounded bg-zinc-50 p-2 text-[11px] dark:bg-zinc-950">
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono">
        {gap !== null && (
          <span>프리갭 <b className={gap >= 0 ? "text-emerald-600" : "text-red-600"}>{gap >= 0 ? "+" : ""}{gap.toFixed(2)}%</b></span>
        )}
        {pmRvol !== null && pmRvol > 0 && (
          <span>프리상대거래량 <b>{pmRvol.toFixed(2)}x</b></span>
        )}
        {orbHigh !== null && (
          <span>15분 고가 <b>${orbHigh.toFixed(2)}</b></span>
        )}
        {orbLow !== null && (
          <span>15분 저가 <b>${orbLow.toFixed(2)}</b></span>
        )}
        {vwap !== null && (
          <span>세션 VWAP <b>${vwap.toFixed(2)}</b></span>
        )}
        {intraRvol !== null && (
          <span>장중 상대거래량 <b>{intraRvol.toFixed(2)}x</b></span>
        )}
      </div>
      {catalystSummary && (
        <p className="mt-1 text-zinc-600 dark:text-zinc-400">
          📰 <span title={catalystSummary}>{catalystSummary.length > 60 ? catalystSummary.slice(0, 60) + "…" : catalystSummary}</span>
        </p>
      )}
      {failReasons && failReasons.length > 0 && (
        <ul className="mt-1 text-red-600 dark:text-red-400">
          {failReasons.map((r, i) => (
            <li key={i}>• {r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
