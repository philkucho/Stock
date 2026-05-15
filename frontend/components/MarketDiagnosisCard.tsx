"use client";

import { useEffect, useState } from "react";

import {
  fetchMarketDiagnosis,
  type MarketDiagnosisResponse,
  type DiagnosisSignal,
} from "@/lib/api";

/**
 * 매매 plan 결정 직전 상단 배너로 표시되는 진단 카드.
 *
 * 사용처:
 *   - /trading 페이지 상단 (매매 결정 직전 한눈에)
 *   - /market 페이지 (자세한 분석 view)
 *
 * variant:
 *   - "banner": 컴팩트 (요약 + 시그널 5~6개 가로)
 *   - "full":   상세 (시그널 카드 + 5 시나리오 + 권장 행동)
 */
export default function MarketDiagnosisCard({
  variant = "banner",
  diagnosisDate,
}: {
  variant?: "banner" | "full";
  diagnosisDate?: string;
}) {
  const [data, setData] = useState<MarketDiagnosisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    fetchMarketDiagnosis(diagnosisDate)
      .then((d) => setData(d))
      .catch((e) => setError(String(e)));
  }, [diagnosisDate]);

  if (error)
    return (
      <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
        진단 fetch 실패: {error}
      </div>
    );

  if (!data) return <SkeletonBanner />;

  return variant === "banner" ? <BannerView data={data} /> : <FullView data={data} />;
}

function verdictStyle(verdict: MarketDiagnosisResponse["verdict"]) {
  switch (verdict) {
    case "defensive":
      return {
        border: "border-red-400 dark:border-red-700",
        bg: "bg-red-50 dark:bg-red-950/40",
        text: "text-red-700 dark:text-red-300",
        emoji: "🛑",
      };
    case "warning":
      return {
        border: "border-amber-400 dark:border-amber-700",
        bg: "bg-amber-50 dark:bg-amber-950/40",
        text: "text-amber-700 dark:text-amber-300",
        emoji: "⚠️",
      };
    default:
      return {
        border: "border-emerald-400 dark:border-emerald-700",
        bg: "bg-emerald-50 dark:bg-emerald-950/40",
        text: "text-emerald-700 dark:text-emerald-300",
        emoji: "✅",
      };
  }
}

function signalLevelStyle(level: DiagnosisSignal["level"]) {
  switch (level) {
    case "danger":
      return "bg-red-100 text-red-700 border-red-300 dark:bg-red-950 dark:text-red-300 dark:border-red-900";
    case "warning":
      return "bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900";
    default:
      return "bg-zinc-100 text-zinc-700 border-zinc-300 dark:bg-zinc-800 dark:text-zinc-300 dark:border-zinc-700";
  }
}

function BannerView({ data }: { data: MarketDiagnosisResponse }) {
  const v = verdictStyle(data.verdict);
  return (
    <section
      className={`rounded-lg border-2 ${v.border} ${v.bg} p-4`}
      aria-label="오늘의 시장 진단"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className={`text-lg font-bold ${v.text}`}>
            {v.emoji} 오늘 시장 진단 — {data.verdict_ko}
          </h2>
          <p className={`text-sm ${v.text}`}>{data.verdict_summary}</p>
        </div>
        <span className="text-xs text-zinc-600 dark:text-zinc-400">
          {data.diagnosis_date} · 시그널 {data.signal_count_triggered}/
          {data.signal_count_total} trigger
        </span>
      </div>

      <p className="mt-2 text-sm text-zinc-800 dark:text-zinc-200">
        <b>권장:</b> {data.recommendation}
      </p>

      <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
        {data.signals.map((s) => (
          <SignalChip key={s.key} signal={s} />
        ))}
      </div>
    </section>
  );
}

function SignalChip({ signal }: { signal: DiagnosisSignal }) {
  const cls = signalLevelStyle(signal.level);
  return (
    <div
      className={`rounded border px-2 py-1.5 text-xs ${cls}`}
      title={signal.note ?? signal.threshold_ko}
    >
      <p className="text-[10px] opacity-80">{signal.label_ko}</p>
      <p className="font-mono font-bold">{signal.value}</p>
    </div>
  );
}

function FullView({ data }: { data: MarketDiagnosisResponse }) {
  const v = verdictStyle(data.verdict);
  return (
    <div className="space-y-5">
      <section className={`rounded-lg border-2 ${v.border} ${v.bg} p-5`}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 className={`text-2xl font-bold ${v.text}`}>
              {v.emoji} {data.verdict_ko}
            </h2>
            <p className={`text-base ${v.text}`}>{data.verdict_summary}</p>
          </div>
          <span className="text-sm text-zinc-600 dark:text-zinc-400">
            {data.diagnosis_date} · 시그널 {data.signal_count_triggered}/
            {data.signal_count_total} trigger
          </span>
        </div>
        <p className="mt-3 text-base text-zinc-800 dark:text-zinc-200">
          <b>권장 행동:</b> {data.recommendation}
        </p>
      </section>

      <section>
        <h2 className="mb-3 text-xl font-semibold text-black dark:text-zinc-50">
          🔬 7개 시그널 평가
        </h2>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {data.signals.map((s) => (
            <SignalCard key={s.key} signal={s} />
          ))}
        </div>
      </section>

      {data.possibilities.length > 0 && (
        <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-2 text-lg font-semibold text-black dark:text-zinc-50">
            🧭 v10 picks 적을 때의 5가지 가능성
          </h2>
          <p className="mb-3 text-xs text-zinc-500">
            v10 picks가 적다고 무조건 약세장은 아님. 다음 중 어느 시나리오인지 다른 시그널과 종합 판단.
          </p>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {data.possibilities.map((p, i) => (
              <article
                key={i}
                className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm dark:border-zinc-800 dark:bg-zinc-950"
              >
                <h3 className="font-semibold">{p.title}</h3>
                <p className="text-xs text-zinc-600 dark:text-zinc-400">
                  {p.state}
                </p>
                <p className="mt-1 text-xs text-zinc-500 italic">예: {p.example}</p>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function SignalCard({ signal }: { signal: DiagnosisSignal }) {
  const cls = signalLevelStyle(signal.level);
  return (
    <article className={`rounded-lg border p-3 ${cls}`}>
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">{signal.label_ko}</h3>
        <span className="text-[10px] opacity-70">{signal.threshold_ko}</span>
      </div>
      <p className="mt-1 font-mono text-xl font-bold">{signal.value}</p>
      {signal.note && (
        <p className="mt-1 text-xs opacity-80">{signal.note}</p>
      )}
    </article>
  );
}

function SkeletonBanner() {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <p className="text-sm text-zinc-500">시장 진단 평가 중…</p>
    </div>
  );
}
