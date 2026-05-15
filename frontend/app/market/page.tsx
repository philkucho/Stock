"use client";

import { useState } from "react";

import TopNav from "@/components/TopNav";
import MarketDiagnosisCard from "@/components/MarketDiagnosisCard";

function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function MarketPage() {
  const [date, setDate] = useState<string>(todayISO());

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <div className="mx-auto max-w-7xl space-y-5">
        <TopNav />
        <header className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              🧭 오늘의 시장 상황
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              7개 시그널 자동 평가 — 매매 plan 결정 직전 한눈에. <b>장 open 전(09:00 ET log 직후)</b> 자동 갱신.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-zinc-600 dark:text-zinc-400">날짜</span>
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              max={todayISO()}
              className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
        </header>

        <MarketDiagnosisCard variant="full" diagnosisDate={date} />

        <section className="rounded-lg border border-zinc-200 bg-white p-4 text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
          <h2 className="mb-2 text-base font-semibold text-black dark:text-zinc-50">
            📚 진단 판정 기준
          </h2>
          <ul className="ml-4 list-disc space-y-1">
            <li>
              <b className="text-emerald-700 dark:text-emerald-300">정상 (normal)</b>: 0~2 시그널 trigger — auto-trade 그대로 진행
            </li>
            <li>
              <b className="text-amber-700 dark:text-amber-300">주의 (warning)</b>: 3~4 시그널 trigger — auto-trade 작동하되 사용자 review 권장
            </li>
            <li>
              <b className="text-red-700 dark:text-red-300">방어 (defensive)</b>: regime defensive 또는 5+ 시그널 trigger — long 자동 차단, 매매 보류
            </li>
          </ul>
          <p className="mt-3 text-xs text-zinc-500">
            진단은 09:00 ET log phase 직후 자동 산출 (system_pick_logs + regime + market_context).
            매매 plan 결정 시 /trading 페이지 상단에도 동일 진단 카드가 표시됨.
          </p>
        </section>
      </div>
    </main>
  );
}
