"use client";

// AI 자문 에이전트 morning brief — picks 페이지 상단에 표시.
// 폴링 15초마다 /api/advisor/recommendations/today → status='pending'인 항목만 액션 가능.
// 백엔드는 Telegram 봇과 동일한 추천 데이터를 공유 — 어느 쪽에서 결정해도 양쪽이 동기화됨.

import { useEffect, useState } from "react";
import {
  type AdvisorRecommendation,
  approveAdvisorRecommendation,
  fetchAdvisorRecommendationsToday,
  rejectAdvisorRecommendation,
  triggerMorningBrief,
} from "@/lib/advisor";

const STATUS_LABEL: Record<string, { text: string; color: string }> = {
  pending: { text: "승인 대기", color: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  approved: { text: "승인됨", color: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" },
  rejected: { text: "거부됨", color: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400" },
  expired: { text: "만료", color: "bg-zinc-100 text-zinc-500 dark:bg-zinc-900 dark:text-zinc-500" },
  executed: { text: "발주 완료", color: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" },
};

const REC_TYPE_LABEL: Record<string, string> = {
  morning: "장 시작 전 자문",
  intraday_entry: "장중 신규 진입",
  intraday_add: "장중 추가 매수",
  intraday_exit: "장중 청산",
};

function fmtPrice(v: string | null): string {
  if (!v) return "—";
  const n = parseFloat(v);
  if (Number.isNaN(n)) return "—";
  return `$${n.toFixed(2)}`;
}

function fmtConfidence(v: string | null): { pct: number; color: string } {
  const n = parseFloat(v ?? "0");
  const pct = Math.round(n * 100);
  let color = "text-zinc-500";
  if (pct >= 75) color = "text-emerald-600 dark:text-emerald-400";
  else if (pct >= 60) color = "text-blue-600 dark:text-blue-400";
  else if (pct >= 50) color = "text-amber-600 dark:text-amber-400";
  else color = "text-zinc-500";
  return { pct, color };
}

function expiresInSec(expiresAt: string): number {
  const target = new Date(expiresAt).getTime();
  const now = Date.now();
  return Math.max(0, Math.floor((target - now) / 1000));
}

export default function MorningBriefCard() {
  const [recs, setRecs] = useState<AdvisorRecommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [triggerLoading, setTriggerLoading] = useState(false);
  const [tickKey, setTickKey] = useState(0); // expires countdown 갱신용

  async function load() {
    try {
      const data = await fetchAdvisorRecommendationsToday();
      setRecs(data);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, []);

  // 1초 ticker — 만료 카운트다운 렌더 트리거. setState만 함, 네트워크 X.
  useEffect(() => {
    const id = setInterval(() => setTickKey((k) => k + 1), 1000);
    return () => clearInterval(id);
  }, []);

  async function handleTrigger() {
    setTriggerLoading(true);
    setError(null);
    try {
      await triggerMorningBrief({ notifyTelegram: true });
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setTriggerLoading(false);
    }
  }

  async function handleApprove(recId: number) {
    try {
      await approveAdvisorRecommendation(recId);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleReject(recId: number) {
    const reason = window.prompt("거부 사유를 짧게 입력해주세요");
    if (!reason || !reason.trim()) return;
    try {
      await rejectAdvisorRecommendation(recId, reason.trim());
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  if (recs === null && !error) {
    return (
      <section className="rounded-lg border border-zinc-200 bg-white p-4 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900">
        AI 자문을 불러오는 중…
      </section>
    );
  }

  const pending = (recs ?? []).filter((r) => r.status === "pending");
  const decided = (recs ?? []).filter((r) => r.status !== "pending");

  // tickKey 참조 — JSX에 안 쓰면 미사용 변수 경고. 1초마다 setState 함으로써 render 강제.
  void tickKey;

  return (
    <section className="rounded-lg border border-purple-200 bg-white p-4 dark:border-purple-900 dark:bg-zinc-900">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded bg-purple-100 px-2 py-0.5 text-xs font-semibold text-purple-800 dark:bg-purple-950 dark:text-purple-300">
            AI 자문
          </span>
          <h2 className="text-lg font-semibold text-black dark:text-zinc-50">
            오늘의 AI 추천
          </h2>
          <span className="text-xs text-zinc-500">
            ({pending.length}건 대기 · {decided.length}건 결정 완료)
          </span>
        </div>
        <button
          onClick={handleTrigger}
          disabled={triggerLoading}
          className="rounded-md border border-purple-300 bg-purple-50 px-3 py-1.5 text-xs text-purple-700 hover:bg-purple-100 disabled:opacity-50 dark:border-purple-800 dark:bg-purple-950 dark:text-purple-300"
          title="AI 자문 재실행 (멱등 — 같은 종목 중복 생성 안 함)"
        >
          {triggerLoading ? "분석 중…" : "↻ AI 자문 다시 요청"}
        </button>
      </header>

      {error && (
        <p className="mt-2 rounded bg-red-50 px-3 py-1.5 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
          ⚠️ {error}
        </p>
      )}

      {pending.length === 0 && decided.length === 0 && (
        <p className="mt-3 rounded bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-950 dark:text-zinc-400">
          오늘 AI 추천이 아직 없습니다. 위 버튼으로 즉시 자문을 요청하거나, 09:25 자동 분석을 기다려주세요.
        </p>
      )}

      {pending.length > 0 && (
        <div className="mt-3 space-y-2">
          {pending.map((r) => (
            <RecommendationRow
              key={r.id}
              rec={r}
              onApprove={() => handleApprove(r.id)}
              onReject={() => handleReject(r.id)}
            />
          ))}
        </div>
      )}

      {decided.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300">
            결정된 추천 {decided.length}건 보기
          </summary>
          <div className="mt-2 space-y-1.5">
            {decided.map((r) => (
              <DecidedRow key={r.id} rec={r} />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

function RecommendationRow({
  rec,
  onApprove,
  onReject,
}: {
  rec: AdvisorRecommendation;
  onApprove: () => void;
  onReject: () => void;
}) {
  const conf = fmtConfidence(rec.confidence);
  const remaining = expiresInSec(rec.expires_at);
  const expired = remaining <= 0;

  return (
    <div className="rounded-md border border-amber-200 bg-amber-50/40 p-3 dark:border-amber-900 dark:bg-amber-950/20">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-base font-bold text-black dark:text-zinc-50">
              {rec.symbol}
            </span>
            <span className="text-xs text-zinc-500">
              {REC_TYPE_LABEL[rec.rec_type] ?? rec.rec_type}
            </span>
            <span className={`font-mono text-sm font-semibold ${conf.color}`}>
              신뢰도 {conf.pct}%
            </span>
          </div>
          <div className="mt-1 grid grid-cols-4 gap-2 text-xs">
            <Cell label="진입" value={fmtPrice(rec.entry_price)} />
            <Cell label="손절" value={fmtPrice(rec.stop_price)} negative />
            <Cell label="1차 목표" value={fmtPrice(rec.target_1r)} positive />
            <Cell label="2차 목표" value={fmtPrice(rec.target_2r)} positive />
          </div>
          {rec.reasoning_text && (
            <p className="mt-2 text-xs leading-relaxed text-zinc-700 dark:text-zinc-300">
              <span className="text-zinc-500">근거: </span>
              {rec.reasoning_text}
            </p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          {expired ? (
            <span className="rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500 dark:bg-zinc-900">
              만료됨
            </span>
          ) : (
            <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-mono text-amber-800 dark:bg-amber-950 dark:text-amber-300">
              남은 시간 {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")}
            </span>
          )}
          <div className="flex gap-1.5">
            <button
              onClick={onApprove}
              disabled={expired}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              ✓ 승인
            </button>
            <button
              onClick={onReject}
              disabled={expired}
              className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
            >
              ✗ 거부
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function DecidedRow({ rec }: { rec: AdvisorRecommendation }) {
  const status = STATUS_LABEL[rec.status] ?? { text: rec.status, color: "bg-zinc-100" };
  return (
    <div className="flex items-center gap-2 rounded border border-zinc-200 bg-zinc-50/50 px-2 py-1 text-xs dark:border-zinc-800 dark:bg-zinc-950/40">
      <span className="font-semibold text-zinc-700 dark:text-zinc-300">{rec.symbol}</span>
      <span className="text-zinc-500">{REC_TYPE_LABEL[rec.rec_type] ?? rec.rec_type}</span>
      <span className="font-mono text-zinc-600 dark:text-zinc-400">
        {fmtPrice(rec.entry_price)}
      </span>
      <span className={`ml-auto rounded px-1.5 py-0.5 ${status.color}`}>
        {status.text}
      </span>
      {rec.trade_plan_id && (
        <span className="text-zinc-500">→ plan #{rec.trade_plan_id}</span>
      )}
    </div>
  );
}

function Cell({
  label,
  value,
  positive,
  negative,
}: {
  label: string;
  value: string;
  positive?: boolean;
  negative?: boolean;
}) {
  const color = positive
    ? "text-emerald-700 dark:text-emerald-400"
    : negative
      ? "text-red-700 dark:text-red-400"
      : "text-zinc-700 dark:text-zinc-300";
  return (
    <div className="rounded border border-zinc-200 bg-white px-2 py-1 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`font-mono text-sm font-semibold ${color}`}>{value}</div>
    </div>
  );
}
