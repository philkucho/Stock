"use client";

import { useEffect, useMemo, useState } from "react";
import TopNav from "@/components/TopNav";
import { fetchActivity, type ActivityResponse, type ActivityEvent } from "@/lib/api";

// 이벤트 type별 아이콘 + 색 매핑
const EVENT_META: Record<string, { icon: string; color: string; label: string }> = {
  "pick.logged":        { icon: "📑", color: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",  label: "Picks 적재" },
  "advisor.recommend":  { icon: "🤖", color: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300", label: "AI 추천" },
  "advisor.decided":    { icon: "✅", color: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300", label: "사용자 결정" },
  "plan.created":       { icon: "📋", color: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300", label: "Plan 생성" },
  "plan.sent":          { icon: "📤", color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300", label: "Plan 발송" },
  "broker.submitted":   { icon: "📤", color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300", label: "주문 발송" },
  "broker.filled":      { icon: "💰", color: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300", label: "체결" },
  "broker.canceled":    { icon: "❌", color: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300", label: "취소" },
  "broker.expired":     { icon: "⌛", color: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400", label: "만료" },
  "outcome.recorded":   { icon: "📊", color: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300", label: "결과 기록" },
  "system.warning":     { icon: "⚠️", color: "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300", label: "시스템 경고" },
};

function todayLocalISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

function formatTimeET(isoUtc: string): string {
  // UTC ISO → ET local time 표시 (브라우저 timezone에 따라 자동 변환되지만, ET 기준이라 가정)
  try {
    const d = new Date(isoUtc);
    return d.toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return isoUtc.slice(11, 19);
  }
}

export default function ActivityPage() {
  const [date, setDate] = useState<string>(todayLocalISO());
  const [symbol, setSymbol] = useState<string>("");
  const [eventTypeFilter, setEventTypeFilter] = useState<string>("");
  const [data, setData] = useState<ActivityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchActivity(date, symbol || undefined);
      setData(r);
      setExpanded(new Set());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, symbol]);

  const filteredEvents = useMemo(() => {
    if (!data) return [];
    if (!eventTypeFilter) return data.events;
    return data.events.filter((e) => e.type === eventTypeFilter);
  }, [data, eventTypeFilter]);

  const eventTypeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    if (!data) return counts;
    for (const e of data.events) counts[e.type] = (counts[e.type] || 0) + 1;
    return counts;
  }, [data]);

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <div className="mx-auto max-w-6xl space-y-6">
        <TopNav />
        <header>
          <h1 className="text-3xl font-bold text-black dark:text-zinc-50">📜 활동 기록</h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            매매·추천·주문·체결의 모든 이벤트를 시간순으로 통합. 시스템 ↔ AI 자문 ↔ Telegram ↔ Broker 흐름 한눈에.
          </p>
        </header>

        {/* 컨트롤 */}
        <section className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <label className="flex items-center gap-2 text-sm">
            <span className="text-zinc-600 dark:text-zinc-400">날짜</span>
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-zinc-600 dark:text-zinc-400">종목</span>
            <select
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
            >
              <option value="">전체</option>
              {data?.symbols.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-zinc-600 dark:text-zinc-400">이벤트 종류</span>
            <select
              value={eventTypeFilter}
              onChange={(e) => setEventTypeFilter(e.target.value)}
              className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
            >
              <option value="">전체 ({data?.events.length ?? 0})</option>
              {Object.entries(eventTypeCounts).map(([type, count]) => (
                <option key={type} value={type}>
                  {EVENT_META[type]?.label ?? type} ({count})
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={load}
            disabled={loading}
            className="rounded border border-blue-300 bg-blue-50 px-3 py-1 text-sm text-blue-700 hover:bg-blue-100 disabled:opacity-50 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300"
          >
            {loading ? "불러오는 중…" : "↻ 새로고침"}
          </button>
        </section>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ {error}
          </div>
        )}

        {/* 요약 카드 */}
        {data && (
          <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5">
            <SummaryCard label="Picks 적재" value={data.summary.picks_count} />
            <SummaryCard label="AI 추천" value={data.summary.advisor_recommendations} />
            <SummaryCard
              label="추천 처리"
              value={`${data.summary.advisor_approved}/${data.summary.advisor_rejected}/${data.summary.advisor_expired}`}
              hint="승인/거부/만료"
            />
            <SummaryCard label="발송된 Plan" value={data.summary.plans_sent} />
            <SummaryCard label="Broker 주문" value={data.summary.broker_orders} />
            <SummaryCard
              label="체결/취소"
              value={`${data.summary.broker_filled}/${data.summary.broker_canceled}`}
              hint="filled/canceled"
            />
            <SummaryCard
              label="실현 손익"
              value={`${data.summary.realized_pnl_usd >= 0 ? "+" : ""}$${data.summary.realized_pnl_usd.toFixed(2)}`}
              positive={data.summary.realized_pnl_usd >= 0}
            />
            <SummaryCard label="등장 종목" value={data.symbols.length} />
          </section>
        )}

        {/* 타임라인 */}
        <section className="rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
          <header className="border-b border-zinc-200 p-4 dark:border-zinc-800">
            <h2 className="text-lg font-semibold text-black dark:text-zinc-50">
              타임라인 ({filteredEvents.length}건)
            </h2>
            <p className="text-xs text-zinc-500">시간순 정렬 · 클릭하면 상세 정보 펼침</p>
          </header>
          <div className="divide-y divide-zinc-200 dark:divide-zinc-800">
            {filteredEvents.length === 0 && (
              <div className="p-6 text-center text-sm text-zinc-500">
                이벤트 없음
              </div>
            )}
            {filteredEvents.map((e, i) => (
              <TimelineRow
                key={i}
                event={e}
                expanded={expanded.has(i)}
                onToggle={() => {
                  const next = new Set(expanded);
                  if (next.has(i)) next.delete(i);
                  else next.add(i);
                  setExpanded(next);
                }}
              />
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}

function SummaryCard({
  label,
  value,
  hint,
  positive,
}: {
  label: string;
  value: number | string;
  hint?: string;
  positive?: boolean;
}) {
  const color =
    positive === true
      ? "text-emerald-600 dark:text-emerald-400"
      : positive === false
      ? "text-red-600 dark:text-red-400"
      : "text-black dark:text-zinc-50";
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className={`mt-1 text-xl font-semibold ${color}`}>{value}</p>
      {hint && <p className="text-[10px] text-zinc-400">{hint}</p>}
    </div>
  );
}

function TimelineRow({
  event,
  expanded,
  onToggle,
}: {
  event: ActivityEvent;
  expanded: boolean;
  onToggle: () => void;
}) {
  const meta = EVENT_META[event.type] ?? {
    icon: "•",
    color: "bg-zinc-100 text-zinc-700",
    label: event.type,
  };
  return (
    <div
      className="cursor-pointer p-3 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-950"
      onClick={onToggle}
    >
      <div className="flex items-start gap-3">
        <span className="font-mono text-xs text-zinc-500 tabular-nums">
          {formatTimeET(event.ts)}
        </span>
        <span className={`rounded px-2 py-0.5 text-xs ${meta.color}`}>
          {meta.icon} {meta.label}
        </span>
        {event.symbol && (
          <span className="rounded bg-zinc-200 px-2 py-0.5 text-xs font-bold dark:bg-zinc-800">
            {event.symbol}
          </span>
        )}
        <span className="flex-1 text-sm text-black dark:text-zinc-100">
          {event.summary}
        </span>
        <span className="text-xs text-zinc-400">{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && Object.keys(event.details).length > 0 && (
        <pre className="mt-2 ml-20 overflow-x-auto rounded bg-zinc-50 p-2 text-[11px] text-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
          {JSON.stringify(event.details, null, 2)}
        </pre>
      )}
    </div>
  );
}
