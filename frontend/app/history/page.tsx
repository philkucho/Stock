"use client";

import { useEffect, useMemo, useState } from "react";

import TopNav from "@/components/TopNav";
import {
  deleteTradePlan,
  fetchOrders,
  fetchPositions,
  fetchTradingPlans,
  type Order,
  type Position,
  type TradePlan,
} from "@/lib/api";
import HelpDrawer from "./HelpDrawer";

const HORIZON_DAYS = 5;
const RANGE_OPTIONS = [7, 30, 90, 180] as const;

function fmtUsd(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toFixed(2)}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function pnlClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return "text-zinc-500";
  return v > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
}

type DailyPoint = { date: string; pnl: number; cum: number };

type StatusTag = { label: string; cls: string; title?: string };

function confirmStatusTag(plan: TradePlan): StatusTag {
  const meta = (plan.score_meta ?? {}) as Record<string, unknown>;
  const reasons = Array.isArray(meta["confirm_fail_reasons"])
    ? (meta["confirm_fail_reasons"] as string[]).join(", ")
    : null;
  const skipReason = typeof meta["confirm_skip_reason"] === "string"
    ? (meta["confirm_skip_reason"] as string)
    : null;
  const orderError = typeof meta["order_error"] === "string"
    ? (meta["order_error"] as string)
    : null;
  const isUserFixed = plan.dispatch_mode === "user_fixed";
  switch (plan.confirm_status) {
    case "watchlist":
      return isUserFixed
        ? {
            label: "📌 09:30 발송 대기 (user_fixed)",
            cls: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
            title: "사용자 입력 plan. 09:30 ET cron에서 사용자가 입력한 entry/stop/target 그대로 bracket order 발송 예정. ORB 평가 없음.",
          }
        : {
            label: "watchlist · 09:45 ORB 평가 대기",
            cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
            title: "스캐너 자동 watchlist. 09:45 ET cron에서 ORB+VWAP+RVOL 게이트 평가 후 발송 결정.",
          };
    case "passed":
      return {
        label: "ORB 통과 · 발송 대기",
        cls: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
        title: "ORB+VWAP+RVOL 통과. AUTO_TRADE_ENABLED=false(dry-run) 상태일 수 있음.",
      };
    case "sent":
      return {
        label: "📤 발송 완료",
        cls: "bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300",
        title: isUserFixed
          ? "09:30 trade phase에서 사용자 입력값 그대로 Alpaca paper에 bracket order 발송됨."
          : "09:45 confirm phase에서 ORB 기반 산출 가격으로 Alpaca paper에 bracket order 발송됨.",
      };
    case "failed":
      return {
        label: reasons ? `🚫 ORB 미통과: ${reasons}` : "🚫 ORB 미통과",
        cls: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
        title: reasons
          ? `09:45 ORB+VWAP+RVOL 게이트 미통과: ${reasons}`
          : "09:45 ORB+VWAP+RVOL 게이트 미통과 (사유 없음).",
      };
    case "skipped": {
      const reason = skipReason || orderError || "사유 미상";
      return {
        label: `⚠ 발송 스킵: ${reason}`,
        cls: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
        title: orderError ? `Alpaca 발송 실패: ${orderError}` : `스킵 사유: ${reason}`,
      };
    }
    default:
      return {
        label: plan.confirm_status,
        cls: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
      };
  }
}

function aggregatePnlByDate(plans: TradePlan[], horizon = HORIZON_DAYS): DailyPoint[] {
  // plan_date 기준으로 그룹핑, 각 plan의 5d outcome PnL 합산.
  const byDate = new Map<string, number>();
  for (const p of plans) {
    const o = p.outcomes.find((x) => x.horizon_days === horizon);
    if (!o) continue;
    const pnl = parseFloat(o.realized_pnl_usd);
    if (Number.isNaN(pnl)) continue;
    byDate.set(p.plan_date, (byDate.get(p.plan_date) ?? 0) + pnl);
  }
  const sorted = Array.from(byDate.entries()).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  let cum = 0;
  return sorted.map(([date, pnl]) => {
    cum += pnl;
    return { date, pnl, cum };
  });
}

function PnlSparkline({ points }: { points: DailyPoint[] }) {
  if (points.length === 0) {
    return (
      <div className="text-sm text-zinc-500 italic">
        완결된 plan이 없어 곡선을 그릴 수 없습니다 (5d outcome 백필 필요).
      </div>
    );
  }
  const W = 600;
  const H = 120;
  const PAD = 8;
  const xs = points.map((_, i) => i);
  const ys = points.map((p) => p.cum);
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(0, ...ys);
  const rangeY = maxY - minY || 1;
  const xStep = points.length > 1 ? (W - 2 * PAD) / (points.length - 1) : 0;
  const toXY = (i: number, y: number) => ({
    x: PAD + i * xStep,
    y: H - PAD - ((y - minY) / rangeY) * (H - 2 * PAD),
  });
  const path = xs
    .map((i) => {
      const { x, y } = toXY(i, ys[i]);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const zeroY = H - PAD - ((0 - minY) / rangeY) * (H - 2 * PAD);
  const last = ys[ys.length - 1];
  const lineColor = last >= 0 ? "#10b981" : "#ef4444";

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-32"
        preserveAspectRatio="none"
      >
        <line
          x1={PAD}
          x2={W - PAD}
          y1={zeroY}
          y2={zeroY}
          stroke="#a1a1aa"
          strokeWidth="0.5"
          strokeDasharray="3,3"
        />
        <path d={path} fill="none" stroke={lineColor} strokeWidth="2" />
      </svg>
      <div className="mt-2 flex justify-between text-xs text-zinc-500">
        <span>
          {points[0].date} → {points[points.length - 1].date}
        </span>
        <span>
          최저 {fmtUsd(Math.min(...ys))} · 최고 {fmtUsd(Math.max(...ys))} · 누적{" "}
          <span className={pnlClass(last)}>{fmtUsd(last)}</span>
        </span>
      </div>
    </div>
  );
}

export default function HistoryPage() {
  const [days, setDays] = useState<number>(30);
  const [plans, setPlans] = useState<TradePlan[] | null>(null);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [positions, setPositions] = useState<Position[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [systemOnly, setSystemOnly] = useState(true);

  async function handleDelete(plan: TradePlan) {
    if (!confirm(`${plan.plan_date} ${plan.symbol} plan을 삭제하시겠습니까?`)) return;
    setDeletingId(plan.id);
    try {
      await deleteTradePlan(plan.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeletingId(null);
    }
  }

  async function load() {
    setLoading(true);
    setError(null);
    // allSettled: 한 fetch가 실패해도 나머지는 표시. 실패한 endpoint를 명시.
    const results = await Promise.allSettled([
      fetchTradingPlans(days),
      fetchOrders({ limit: 100, systemOnly }),
      fetchPositions(),
    ]);
    const labels = ["매매 Plan", "주문 내역", "포지션"];
    const errors: string[] = [];

    if (results[0].status === "fulfilled") setPlans(results[0].value);
    else errors.push(`${labels[0]}: ${results[0].reason?.message ?? String(results[0].reason)}`);

    if (results[1].status === "fulfilled") setOrders(results[1].value);
    else errors.push(`${labels[1]}: ${results[1].reason?.message ?? String(results[1].reason)}`);

    if (results[2].status === "fulfilled") setPositions(results[2].value);
    else errors.push(`${labels[2]}: ${results[2].reason?.message ?? String(results[2].reason)}`);

    setError(errors.length > 0 ? errors.join(" / ") : null);
    setLoading(false);
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, systemOnly]);

  const pnlPoints = useMemo(
    () => (plans ? aggregatePnlByDate(plans) : []),
    [plans],
  );

  // 매매 Plan 내역 — plan_date로 그룹화 (날짜 desc, 그룹 내부는 rank asc)
  const groupedPlans = useMemo(() => {
    if (!plans) return [];
    const map = new Map<string, TradePlan[]>();
    for (const p of plans) {
      const list = map.get(p.plan_date) ?? [];
      list.push(p);
      map.set(p.plan_date, list);
    }
    for (const list of map.values()) list.sort((a, b) => a.rank - b.rank);
    return Array.from(map.entries())
      .sort(([a], [b]) => b.localeCompare(a))
      .map(([date, list]) => {
        const totalAmount = list.reduce((s, p) => s + parseFloat(p.amount_usd), 0);
        const totalRisk = list.reduce((s, p) => s + parseFloat(p.risk_usd), 0);
        return { date, plans: list, totalAmount, totalRisk };
      });
  }, [plans]);

  const summary = useMemo(() => {
    if (!plans) return null;
    let withOutcome = 0;
    let totalPnl = 0;
    let wins = 0;
    let hitTarget = 0;
    let hitStop = 0;
    const rets: number[] = [];
    for (const p of plans) {
      const o = p.outcomes.find((x) => x.horizon_days === HORIZON_DAYS);
      if (!o) continue;
      withOutcome += 1;
      const pnl = parseFloat(o.realized_pnl_usd);
      const ret = parseFloat(o.pct_return);
      if (!Number.isNaN(pnl)) totalPnl += pnl;
      if (!Number.isNaN(ret)) {
        rets.push(ret);
        if (ret > 0) wins += 1;
      }
      if (o.hit_target_1r) hitTarget += 1;
      if (o.hit_stop) hitStop += 1;
    }
    const avgRet = rets.length ? rets.reduce((a, b) => a + b, 0) / rets.length : 0;
    const winRate = rets.length ? (wins / rets.length) * 100 : 0;
    return {
      total: plans.length,
      withOutcome,
      totalPnl,
      avgRet,
      winRate,
      hitTarget,
      hitStop,
    };
  }, [plans]);

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-6xl space-y-6">
        <TopNav />
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              📒 매매 History
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              매매 Plan + 브로커 주문 + PnL 누적 곡선 ({HORIZON_DAYS}d horizon
              기준)
            </p>
          </div>
          <div className="flex items-center gap-2">
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
            <label className="text-sm text-zinc-600 dark:text-zinc-400">
              기간:
              <select
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                className="ml-2 rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              >
                {RANGE_OPTIONS.map((d) => (
                  <option key={d} value={d}>
                    최근 {d}일
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={load}
              disabled={loading}
              className="rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-sm hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800"
            >
              {loading ? "로딩..." : "새로고침"}
            </button>
          </div>
        </header>

        {error && (
          <div className="rounded-lg border border-rose-300 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-300">
            ✗ {error}
          </div>
        )}

        {/* PnL 요약 + 곡선 */}
        <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-4 text-lg font-semibold text-black dark:text-zinc-50">
            누적 PnL
          </h2>
          {summary && (
            <div className="mb-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
              <div>
                <div className="text-zinc-500">Plan 수 (총 / 완결)</div>
                <div className="font-mono text-lg text-black dark:text-zinc-50">
                  {summary.total} / {summary.withOutcome}
                </div>
              </div>
              <div>
                <div className="text-zinc-500">누적 실현 PnL</div>
                <div className={`font-mono text-lg ${pnlClass(summary.totalPnl)}`}>
                  {fmtUsd(summary.totalPnl)}
                </div>
              </div>
              <div>
                <div className="text-zinc-500">평균 수익률 / 승률</div>
                <div className="font-mono text-lg">
                  <span className={pnlClass(summary.avgRet)}>
                    {fmtPct(summary.avgRet)}
                  </span>{" "}
                  <span className="text-zinc-500 text-sm">
                    ({summary.winRate.toFixed(0)}%)
                  </span>
                </div>
              </div>
              <div>
                <div className="text-zinc-500">Target 1R / Stop 도달</div>
                <div className="font-mono text-lg">
                  <span className="text-emerald-600 dark:text-emerald-400">
                    {summary.hitTarget}
                  </span>
                  <span className="text-zinc-400"> / </span>
                  <span className="text-rose-600 dark:text-rose-400">
                    {summary.hitStop}
                  </span>
                </div>
              </div>
            </div>
          )}
          <PnlSparkline points={pnlPoints} />
        </section>

        {/* 매매 Plan 내역 */}
        <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-4 text-lg font-semibold text-black dark:text-zinc-50">
            매매 Plan 내역
          </h2>
          {plans && plans.length === 0 && (
            <p className="text-sm text-zinc-500 italic">
              해당 기간에 저장된 매매 Plan이 없습니다.
            </p>
          )}
          {plans && plans.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-zinc-200 text-left text-xs uppercase text-zinc-500 dark:border-zinc-800">
                  <tr>
                    <th className="py-2 pr-3">심볼</th>
                    <th className="py-2 pr-3">시스템</th>
                    <th className="py-2 pr-3 text-right">Rank</th>
                    <th className="py-2 pr-3 text-right">금액 ($)</th>
                    <th className="py-2 pr-3 text-right">주식수</th>
                    <th className="py-2 pr-3 text-right">Entry</th>
                    <th className="py-2 pr-3 text-right">Stop</th>
                    <th className="py-2 pr-3 text-right">1차 목표</th>
                    <th className="py-2 pr-3 text-right">2차 목표</th>
                    <th className="py-2 pr-3 text-right">{HORIZON_DAYS}d 수익률</th>
                    <th className="py-2 pr-3 text-right">{HORIZON_DAYS}d PnL</th>
                    <th className="py-2 pr-3 text-center">결과</th>
                    <th className="py-2 pr-1 text-center">삭제</th>
                  </tr>
                </thead>
                <tbody>
                  {groupedPlans.flatMap((group) => [
                    <tr key={`hdr-${group.date}`} className="border-b border-zinc-200 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-800/60">
                      <td colSpan={13} className="px-3 py-2">
                        <div className="flex items-baseline gap-3 text-sm">
                          <span className="font-mono font-semibold text-zinc-900 dark:text-zinc-100">
                            📅 {group.date}
                          </span>
                          <span className="text-xs text-zinc-500">
                            {group.plans.length}개 plan
                          </span>
                          <span className="ml-auto text-xs text-zinc-600 dark:text-zinc-300">
                            노출 <span className="font-mono">${group.totalAmount.toFixed(2)}</span>
                            <span className="mx-2 text-zinc-400">·</span>
                            위험 <span className="font-mono text-rose-600 dark:text-rose-400">-${group.totalRisk.toFixed(2)}</span>
                          </span>
                        </div>
                      </td>
                    </tr>,
                    ...group.plans.map((p) => {
                      const o = p.outcomes.find((x) => x.horizon_days === HORIZON_DAYS);
                      const ret = o ? parseFloat(o.pct_return) : null;
                      const pnl = o ? parseFloat(o.realized_pnl_usd) : null;
                      const tag: StatusTag = o
                        ? o.hit_target_1r && o.hit_target_2r
                          ? { label: "🎯🎯 1차+2차 도달", cls: "bg-emerald-200 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200" }
                          : o.hit_target_1r
                            ? o.hit_stop
                              ? { label: "🎯 1차 청산 + 잔여 손절", cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300" }
                              : { label: "🎯 1차 청산", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" }
                            : o.hit_stop
                              ? { label: "🛑 손절", cls: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300" }
                              : { label: "보유 중", cls: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400" }
                        : confirmStatusTag(p);
                      // 시스템 비교 그래프 색과 일치: v10=빨강, v9=호박, intraday=분홍
                      const sysCfg = p.system_source === "intraday_v1"
                        ? { label: "intraday_v1", title: "단타 (5-Model Stack)", cls: "bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300" }
                        : p.system_source === "v9_fallback"
                          ? { label: "v9 fallback", title: "통합 v10이 부족할 때 v9에서 보충된 종목", cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300" }
                          : { label: "v10", title: "통합 v10 기본 추천", cls: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300" };
                      return (
                        <tr key={p.id} className="border-b border-zinc-100 dark:border-zinc-800">
                          <td className="py-2 pr-3 font-bold">{p.symbol}</td>
                          <td className="py-2 pr-3">
                            <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${sysCfg.cls}`} title={sysCfg.title}>
                              {sysCfg.label}
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-right">{p.rank}</td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {parseFloat(p.amount_usd).toFixed(0)}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">{p.shares}</td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {parseFloat(p.entry_price).toFixed(2)}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono text-rose-600 dark:text-rose-400">
                            {parseFloat(p.stop_price).toFixed(2)}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono text-emerald-600 dark:text-emerald-400">
                            {parseFloat(p.target_1r).toFixed(2)}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono text-emerald-700 dark:text-emerald-300">
                            {parseFloat(p.target_2r).toFixed(2)}
                          </td>
                          <td className={`py-2 pr-3 text-right font-mono ${pnlClass(ret)}`}>
                            {fmtPct(ret)}
                          </td>
                          <td className={`py-2 pr-3 text-right font-mono ${pnlClass(pnl)}`}>
                            {fmtUsd(pnl)}
                          </td>
                          <td className="py-2 pr-3 text-center">
                            <span
                              className={`rounded px-2 py-0.5 text-xs ${tag.cls}`}
                              title={tag.title}
                            >
                              {tag.label}
                            </span>
                          </td>
                          <td className="py-2 pr-1 text-center">
                            <button
                              onClick={() => handleDelete(p)}
                              disabled={deletingId === p.id}
                              className="rounded px-2 py-0.5 text-xs text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:text-rose-400 dark:hover:bg-rose-950"
                              title="이 plan 삭제"
                            >
                              {deletingId === p.id ? "…" : "✕"}
                            </button>
                          </td>
                        </tr>
                      );
                    }),
                  ])}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Orders & Fills (broker) */}
        <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold text-black dark:text-zinc-50">
              브로커 주문 (Alpaca paper)
            </h2>
            <label className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
              <input
                type="checkbox"
                checked={systemOnly}
                onChange={(e) => setSystemOnly(e.target.checked)}
                className="h-3.5 w-3.5"
              />
              시스템 발송분만 (trade_plans.broker_order_ids)
            </label>
          </div>
          {orders && orders.length === 0 && (
            <p className="text-sm text-zinc-500 italic">
              {systemOnly
                ? "시스템이 발송한 주문이 없습니다. 발송 경로 2개: ① user_fixed plan은 09:30 cron이 입력값 그대로 발송 ② orb_auto watchlist는 09:45 cron이 ORB+VWAP+RVOL 통과한 종목만 발송. 종목별 처리 결과는 위 매매 Plan 테이블의 '결과' 배지에서 확인. 체크박스를 끄면 Alpaca 계좌의 모든 주문(테스트·수동 포함)을 볼 수 있습니다."
                : "Alpaca paper 계좌에 주문이 없습니다."}
            </p>
          )}
          {orders && orders.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-zinc-200 text-left text-xs uppercase text-zinc-500 dark:border-zinc-800">
                  <tr>
                    <th className="py-2 pr-3">생성</th>
                    <th className="py-2 pr-3">심볼</th>
                    <th className="py-2 pr-3">Side</th>
                    <th className="py-2 pr-3 text-right">수량</th>
                    <th className="py-2 pr-3 text-right">Entry</th>
                    <th className="py-2 pr-3 text-right">Stop</th>
                    <th className="py-2 pr-3 text-right">TP</th>
                    <th className="py-2 pr-3">상태</th>
                    <th className="py-2 pr-3">Broker ID</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => {
                    const statusCls =
                      o.status === "filled"
                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                        : o.status === "canceled" || o.status === "expired" || o.status === "rejected"
                          ? "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                          : o.status === "new" || o.status === "accepted" || o.status === "pending_new"
                            ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                            : "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300";
                    return (
                      <tr key={o.id} className="border-b border-zinc-100 dark:border-zinc-800">
                        <td className="py-2 pr-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                          {o.created_at.replace("T", " ").slice(0, 19)}
                        </td>
                        <td className="py-2 pr-3 font-bold">{o.symbol}</td>
                        <td className={`py-2 pr-3 font-bold ${o.side === "BUY" ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
                          {o.side}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono">
                          {parseFloat(o.quantity).toFixed(0)}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono">
                          {o.entry_price ? parseFloat(o.entry_price).toFixed(2) : "—"}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-rose-600 dark:text-rose-400">
                          {o.stop_loss_price ? parseFloat(o.stop_loss_price).toFixed(2) : "—"}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-emerald-600 dark:text-emerald-400">
                          {o.take_profit_price ? parseFloat(o.take_profit_price).toFixed(2) : "—"}
                        </td>
                        <td className="py-2 pr-3 text-xs">
                          <span className={`rounded px-2 py-0.5 ${statusCls}`}>
                            {o.status}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-xs text-zinc-500 font-mono" title={o.broker_order_id ?? ""}>
                          {o.broker_order_id ? o.broker_order_id.slice(0, 8) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Open positions */}
        <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-4 text-lg font-semibold text-black dark:text-zinc-50">
            현재 포지션
          </h2>
          {positions && positions.length === 0 && (
            <p className="text-sm text-zinc-500 italic">보유 포지션 없음.</p>
          )}
          {positions && positions.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-zinc-200 text-left text-xs uppercase text-zinc-500 dark:border-zinc-800">
                  <tr>
                    <th className="py-2 pr-3">계좌</th>
                    <th className="py-2 pr-3">심볼</th>
                    <th className="py-2 pr-3 text-right">수량</th>
                    <th className="py-2 pr-3 text-right">평균단가</th>
                    <th className="py-2 pr-3 text-right">미실현 PnL</th>
                    <th className="py-2 pr-3">갱신</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => {
                    const pnl = parseFloat(p.realized_pnl);
                    return (
                      <tr
                        key={`${p.account}-${p.symbol}`}
                        className="border-b border-zinc-100 dark:border-zinc-800"
                      >
                        <td className="py-2 pr-3 font-mono text-xs text-zinc-500">
                          {p.account}
                        </td>
                        <td className="py-2 pr-3 font-bold">{p.symbol}</td>
                        <td className="py-2 pr-3 text-right font-mono">
                          {parseFloat(p.quantity).toFixed(0)}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono">
                          {parseFloat(p.avg_price).toFixed(2)}
                        </td>
                        <td className={`py-2 pr-3 text-right font-mono ${pnlClass(pnl)}`}>
                          {fmtUsd(pnl)}
                        </td>
                        <td className="py-2 pr-3 font-mono text-xs text-zinc-500">
                          {p.updated_at.replace("T", " ").slice(0, 19)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
