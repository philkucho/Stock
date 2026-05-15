"use client";

import { useEffect, useMemo, useState } from "react";

import TopNav from "@/components/TopNav";
import {
  fetchDailyReview,
  type DailyReviewResponse,
  type ReviewPlanRow,
} from "@/lib/api";

const STATUS_BADGE: Record<
  string,
  { ko: string; cls: string; emoji: string }
> = {
  watchlist: {
    ko: "워치리스트",
    cls: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    emoji: "⏳",
  },
  passed: {
    ko: "확인 통과",
    cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    emoji: "✅",
  },
  failed: {
    ko: "확인 실패",
    cls: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
    emoji: "❌",
  },
  sent: {
    ko: "주문 발송",
    cls: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
    emoji: "📤",
  },
  skipped: {
    ko: "건너뜀",
    cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
    emoji: "↪",
  },
};

const SYSTEM_BADGE: Record<string, { ko: string; cls: string }> = {
  intraday_v1: {
    ko: "단타",
    cls: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  },
  v10: {
    ko: "스윙 v10",
    cls: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
  },
  v9_fallback: {
    ko: "v9 보충",
    cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  },
};

function todayISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

export default function ReviewPage() {
  const [data, setData] = useState<DailyReviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewDate, setReviewDate] = useState<string>(todayISO());

  useEffect(() => {
    setError(null);
    fetchDailyReview(reviewDate)
      .then((d) => setData(d))
      .catch((e) => setError(String(e)));
  }, [reviewDate]);

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <div className="mx-auto max-w-7xl space-y-5">
        <TopNav />
        <header className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              📒 일일 매매 리뷰
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              계획 (워치리스트 + ORB 평가) 대비 실제 (체결 + 실현 손익) 비교 — EOD 한 줄 정리
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-zinc-600 dark:text-zinc-400">날짜</span>
            <input
              type="date"
              value={reviewDate}
              onChange={(e) => setReviewDate(e.target.value)}
              max={todayISO()}
              className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
        </header>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ {error}
          </div>
        )}

        {data === null && !error && <p className="text-zinc-500">불러오는 중…</p>}

        {data && data.summary.watchlist_count === 0 && (
          <div className="rounded-lg border border-zinc-200 bg-white p-8 text-center text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900">
            {reviewDate} 매매 plan이 없습니다. (주말/공휴일 또는 워치리스트 미생성)
          </div>
        )}

        {data && data.summary.watchlist_count > 0 && (
          <>
            <SummaryCards data={data} />
            <FailReasonsCard data={data} />
            <PlansTable plans={data.plans} />
          </>
        )}
      </div>
    </main>
  );
}

function SummaryCards({ data }: { data: DailyReviewResponse }) {
  const s = data.summary;
  const t = data.totals;
  const winRate =
    t.win_count + t.loss_count > 0
      ? (t.win_count / (t.win_count + t.loss_count)) * 100
      : null;
  const pnlClass =
    t.actual_realized_pnl_usd > 0
      ? "text-emerald-600 dark:text-emerald-400"
      : t.actual_realized_pnl_usd < 0
        ? "text-red-600 dark:text-red-400"
        : "text-zinc-500";
  return (
    <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Tile label="워치리스트" value={`${s.watchlist_count}`} sub="단타+스윙" />
      <Tile
        label="확인 결과"
        value={`${s.passed_count}/${s.failed_count}`}
        sub="통과 / 실패"
      />
      <Tile
        label="발송 / 건너뜀"
        value={`${s.sent_count}/${s.skipped_count}`}
        sub="bracket 주문"
      />
      <Tile
        label="실현 손익"
        value={`${t.actual_realized_pnl_usd >= 0 ? "+" : ""}$${t.actual_realized_pnl_usd.toFixed(2)}`}
        sub={
          winRate !== null
            ? `승률 ${winRate.toFixed(0)}% (W${t.win_count}/L${t.loss_count})`
            : "(체결 결과 없음)"
        }
        valueClass={pnlClass}
      />
      <Tile
        label="계획 노출"
        value={`$${t.planned_exposure_usd.toLocaleString()}`}
        sub="planned amount"
      />
      <Tile
        label="계획 위험"
        value={`-$${t.planned_risk_usd.toLocaleString()}`}
        sub="planned risk_usd"
        valueClass="text-red-600 dark:text-red-400"
      />
      <Tile
        label="평균 알파"
        value={
          t.actual_alpha_avg !== null
            ? `${t.actual_alpha_avg >= 0 ? "+" : ""}${t.actual_alpha_avg.toFixed(2)}%`
            : "—"
        }
        sub="vs SPY 1d"
        valueClass={
          t.actual_alpha_avg !== null && t.actual_alpha_avg > 0
            ? "text-emerald-600 dark:text-emerald-400"
            : t.actual_alpha_avg !== null && t.actual_alpha_avg < 0
              ? "text-red-600 dark:text-red-400"
              : ""
        }
      />
      <Tile
        label="리뷰 일자"
        value={data.review_date}
        sub={new Date(data.review_date).toLocaleDateString("ko-KR", {
          weekday: "long",
        })}
      />
    </section>
  );
}

function Tile({
  label,
  value,
  sub,
  valueClass = "",
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
      <p className="text-[11px] uppercase tracking-wide text-zinc-500">
        {label}
      </p>
      <p className={`mt-1 font-mono text-xl font-bold ${valueClass}`}>
        {value}
      </p>
      {sub && <p className="text-[11px] text-zinc-500">{sub}</p>}
    </div>
  );
}

function FailReasonsCard({ data }: { data: DailyReviewResponse }) {
  const reasons = data.summary.fail_reason_counts;
  const entries = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;

  const labelKo: Record<string, string> = {
    orb_break: "ORB 돌파 미달",
    vwap: "VWAP 미달",
    rvol: "장중 거래량 부족",
    range: "Opening Range 폭 부족",
    no_intraday_data: "분봉 데이터 없음",
    evaluation_none: "평가 실패",
    invalid_r: "리스크 단위 무효",
  };

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-2 text-base font-semibold text-black dark:text-zinc-50">
        ❌ 확인 실패 원인 분포
      </h2>
      <div className="flex flex-wrap gap-2">
        {entries.map(([key, count]) => (
          <span
            key={key}
            className="inline-flex items-center gap-1 rounded bg-red-50 px-2 py-1 text-xs dark:bg-red-950/30"
          >
            <span className="text-red-700 dark:text-red-300">
              {labelKo[key] || key}
            </span>
            <span className="font-mono font-bold text-red-700 dark:text-red-300">
              {count}
            </span>
          </span>
        ))}
      </div>
    </section>
  );
}

function PlansTable({ plans }: { plans: ReviewPlanRow[] }) {
  const sortedPlans = useMemo(
    () =>
      [...plans].sort((a, b) => {
        // sent > passed > failed > watchlist 순 + rank
        const order = ["sent", "passed", "failed", "skipped", "watchlist"];
        const ia = order.indexOf(a.confirm_status);
        const ib = order.indexOf(b.confirm_status);
        if (ia !== ib) return ia - ib;
        return a.rank - b.rank;
      }),
    [plans],
  );

  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold text-black dark:text-zinc-50">
        🎯 종목별 상세
      </h2>
      <div className="grid gap-3">
        {sortedPlans.map((p) => (
          <PlanRow key={`${p.symbol}-${p.rank}`} plan={p} />
        ))}
      </div>
    </section>
  );
}

function PlanRow({ plan }: { plan: ReviewPlanRow }) {
  const statusCfg = STATUS_BADGE[plan.confirm_status] ?? STATUS_BADGE.watchlist;
  const systemCfg = SYSTEM_BADGE[plan.system_source] ?? SYSTEM_BADGE.v10;
  const oneR = plan.planned_entry - plan.planned_stop;
  const hasOrb = plan.orb_high !== null;

  const alphaPositive = plan.actual_alpha !== null && plan.actual_alpha > 0;
  const alphaNegative = plan.actual_alpha !== null && plan.actual_alpha < 0;
  const alphaClass = alphaPositive
    ? "text-emerald-600 dark:text-emerald-400"
    : alphaNegative
      ? "text-red-600 dark:text-red-400"
      : "text-zinc-500";

  return (
    <article className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <span className="text-xs text-zinc-500">#{plan.rank}</span>
          <span className="text-2xl font-bold text-black dark:text-zinc-50">
            {plan.symbol}
          </span>
          {plan.sector && (
            <span className="text-xs text-zinc-500">{plan.sector}</span>
          )}
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] ${systemCfg.cls}`}
          >
            {systemCfg.ko}
          </span>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] ${statusCfg.cls}`}
          >
            {statusCfg.emoji} {statusCfg.ko}
          </span>
        </div>
        <div className="text-right">
          <p className="font-mono text-lg text-blue-600 dark:text-blue-400">
            점수 {plan.composite_score.toFixed(1)}
          </p>
        </div>
      </div>

      {plan.catalyst_summary && (
        <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
          📰 <span title={plan.catalyst_summary}>{plan.catalyst_summary}</span>
        </p>
      )}

      {/* Signals row: 프리갭 + 프리RVOL + ORB + VWAP + 장중 RVOL */}
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 rounded bg-zinc-50 p-2 font-mono text-[11px] dark:bg-zinc-950 md:grid-cols-4">
        {plan.premarket_gap_pct !== null && (
          <span>
            프리갭{" "}
            <b
              className={
                plan.premarket_gap_pct >= 0
                  ? "text-emerald-600"
                  : "text-red-600"
              }
            >
              {plan.premarket_gap_pct >= 0 ? "+" : ""}
              {plan.premarket_gap_pct.toFixed(2)}%
            </b>
          </span>
        )}
        {plan.premarket_rvol !== null && plan.premarket_rvol > 0 && (
          <span>
            프리RVOL <b>{plan.premarket_rvol.toFixed(2)}×</b>
          </span>
        )}
        {hasOrb && (
          <>
            <span>
              15분 고가 <b>${plan.orb_high!.toFixed(2)}</b>
            </span>
            <span>
              15분 저가 <b>${plan.orb_low!.toFixed(2)}</b>
            </span>
            {plan.session_vwap !== null && (
              <span>
                세션 VWAP <b>${plan.session_vwap.toFixed(2)}</b>
              </span>
            )}
            {plan.intraday_rvol !== null && (
              <span>
                장중 RVOL <b>{plan.intraday_rvol.toFixed(2)}×</b>
              </span>
            )}
          </>
        )}
      </div>

      {plan.fail_reasons.length > 0 && (
        <ul className="mt-2 text-xs text-red-600 dark:text-red-400">
          {plan.fail_reasons.map((r, i) => (
            <li key={i}>• {r}</li>
          ))}
        </ul>
      )}

      {/* 계획 vs 실제 */}
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded border border-blue-200 bg-blue-50 p-2 text-xs dark:border-blue-900 dark:bg-blue-950/30">
          <h3 className="mb-1 font-semibold text-blue-900 dark:text-blue-300">
            🎯 계획
          </h3>
          <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 font-mono">
            <span className="text-zinc-500">진입가</span>
            <span>${plan.planned_entry.toFixed(2)}</span>
            <span className="text-zinc-500">손절가</span>
            <span className="text-red-600">${plan.planned_stop.toFixed(2)}</span>
            <span className="text-zinc-500">1R / 2R</span>
            <span className="text-emerald-600">
              ${plan.planned_target_1r.toFixed(2)} / $
              {plan.planned_target_2r.toFixed(2)}
            </span>
            <span className="text-zinc-500">수량</span>
            <span>
              {plan.planned_shares}주 ($
              {plan.planned_amount_usd.toFixed(0)})
            </span>
            <span className="text-zinc-500">1R 폭</span>
            <span>${oneR.toFixed(2)}</span>
            <span className="text-zinc-500">총 위험</span>
            <span className="text-red-600">
              -${plan.planned_risk_usd.toFixed(2)}
            </span>
          </dl>
        </div>
        <div
          className={`rounded border p-2 text-xs ${
            plan.actual_alpha === null
              ? "border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/30"
              : alphaPositive
                ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30"
                : "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/30"
          }`}
        >
          <h3 className="mb-1 font-semibold text-zinc-900 dark:text-zinc-100">
            📊 실제
          </h3>
          {plan.actual_alpha === null ? (
            <p className="text-zinc-500">
              {plan.confirm_status === "sent"
                ? "(아직 결과 없음 — 16:30 backfill 후 채워짐)"
                : "(발송 안 됨 → 결과 없음)"}
            </p>
          ) : (
            <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 font-mono">
              <span className="text-zinc-500">종가</span>
              <span>${plan.actual_exit_price?.toFixed(2)}</span>
              <span className="text-zinc-500">수익률</span>
              <span className={alphaClass}>
                {plan.actual_pct_return! >= 0 ? "+" : ""}
                {plan.actual_pct_return!.toFixed(2)}%
              </span>
              <span className="text-zinc-500">알파 (vs SPY)</span>
              <span className={alphaClass}>
                {plan.actual_alpha! >= 0 ? "+" : ""}
                {plan.actual_alpha!.toFixed(2)}%
              </span>
              <span className="text-zinc-500">실현 손익</span>
              <span className={alphaClass}>
                {plan.actual_realized_pnl! >= 0 ? "+" : ""}$
                {plan.actual_realized_pnl!.toFixed(2)}
              </span>
              <span className="text-zinc-500">목표 도달</span>
              <span>
                {plan.hit_target_2r
                  ? "2R ✅"
                  : plan.hit_target_1r
                    ? "1R ✅"
                    : plan.hit_stop
                      ? "손절 ❌"
                      : "—"}
              </span>
              <span className="text-zinc-500">청산 수량</span>
              <span>
                1차 {plan.qty_sold_at_1r}주 / 2차 {plan.qty_sold_at_2r}주
              </span>
            </dl>
          )}
        </div>
      </div>
    </article>
  );
}
