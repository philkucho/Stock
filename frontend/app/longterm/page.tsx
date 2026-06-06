"use client";

import { useEffect, useState } from "react";

import TopNav from "@/components/TopNav";
import {
  fetchLongtermCurrent,
  refreshLongterm,
  type LongtermCurrent,
  type LongtermPick,
} from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  new: "신규",
  hold: "유지",
  exit_suggested: "이탈권고",
  exited: "청산",
};

const STATUS_COLOR: Record<string, string> = {
  new: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  hold: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  exit_suggested: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  exited: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
};

const ACTION_LABEL: Record<string, string> = {
  BUY: "매수",
  HOLD: "보유",
  TRIM: "부분청산",
  SELL: "전량청산",
};

const ACTION_COLOR: Record<string, string> = {
  BUY: "bg-emerald-600 text-white",
  HOLD: "bg-sky-600 text-white",
  TRIM: "bg-amber-600 text-white",
  SELL: "bg-rose-600 text-white",
};

const GATE_LABEL: Record<string, string> = {
  stage2: "Stage 2",
  above_ma200: "200일선 위",
  near_52w_high: "52주 고가",
  adv_ok: "거래대금",
};

function GateBadges({ gates }: { gates: Record<string, boolean> }) {
  const keys = ["stage2", "above_ma200", "near_52w_high", "adv_ok"];
  return (
    <div className="flex flex-wrap gap-1">
      {keys.map((k) => {
        const passed = gates[k];
        const cls = passed
          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
          : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-500";
        return (
          <span
            key={k}
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${cls}`}
            title={passed ? `${GATE_LABEL[k]} 통과` : `${GATE_LABEL[k]} 미통과`}
          >
            {GATE_LABEL[k] ?? k}
          </span>
        );
      })}
    </div>
  );
}

function fmtPct(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

function fmtScore(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (Number.isNaN(n)) return "—";
  return n.toFixed(2);
}

export default function LongtermPage() {
  const [data, setData] = useState<LongtermCurrent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchLongtermCurrent();
      setData(d);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function onRefresh(dryRun: boolean) {
    setRefreshing(true);
    setRefreshNote(null);
    try {
      const r = await refreshLongterm(dryRun);
      setRefreshNote(
        `${dryRun ? "Dry-run " : ""}완료 — ${r.pick_count}개 picks, regime=${r.defensive ? "defensive" : "ok"}, DB ${r.db_inserted}건 저장`,
      );
      if (!dryRun) await load();
    } catch (e) {
      setRefreshNote(`오류: ${(e as Error).message}`);
    } finally {
      setRefreshing(false);
    }
  }

  const activePicks: LongtermPick[] = (data?.picks ?? []).filter(
    (p) => p.status !== "exited",
  );
  const exitedPicks: LongtermPick[] = (data?.picks ?? []).filter(
    (p) => p.status === "exited",
  );

  return (
    <main className="mx-auto max-w-7xl space-y-6 p-6">
      <TopNav />

      <header className="space-y-2">
        <h1 className="text-2xl font-bold">📅 중장기 추천 (Fidelity 매수 가이드)</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          3~12개월 보유 목적의 정량 시그널 기반 추천. Stage 2 추세 템플릿 + IBD 상대강도 + 12개월
          모멘텀. 매월 첫 거래일 자동 갱신 · Fidelity 계좌 수동 발주용.
        </p>
      </header>

      {loading && <div className="text-sm text-zinc-500">불러오는 중…</div>}
      {error && (
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
          오류: {error}
        </div>
      )}

      {data && (
        <>
          {/* 상단 요약 */}
          <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
              <div className="text-xs text-zinc-500">기준 월</div>
              <div className="text-lg font-semibold">
                {data.pick_month ?? "—"}
              </div>
            </div>
            <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
              <div className="text-xs text-zinc-500">시장 국면</div>
              <div
                className={`text-lg font-semibold ${data.regime === "defensive" ? "text-rose-600" : "text-emerald-600"}`}
                title={
                  data.regime === "defensive"
                    ? "SPY가 200일선 아래 — 신규 매수 권장 안 함"
                    : "정상 상승 국면"
                }
              >
                {data.regime === "defensive"
                  ? "🛑 방어"
                  : data.regime === "ok"
                  ? "✅ 정상"
                  : "—"}
              </div>
            </div>
            <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 dark:border-emerald-900 dark:bg-emerald-950/40">
              <div className="text-xs text-emerald-700 dark:text-emerald-300">
                신규
              </div>
              <div className="text-2xl font-bold text-emerald-800 dark:text-emerald-200">
                {data.new_count}
              </div>
            </div>
            <div className="rounded-lg border border-sky-300 bg-sky-50 p-3 dark:border-sky-900 dark:bg-sky-950/40">
              <div className="text-xs text-sky-700 dark:text-sky-300">유지</div>
              <div className="text-2xl font-bold text-sky-800 dark:text-sky-200">
                {data.hold_count}
              </div>
            </div>
            <div className="rounded-lg border border-rose-300 bg-rose-50 p-3 dark:border-rose-900 dark:bg-rose-950/40">
              <div className="text-xs text-rose-700 dark:text-rose-300">
                이탈/청산
              </div>
              <div className="text-2xl font-bold text-rose-800 dark:text-rose-200">
                {data.exit_suggested_count + data.exited_count}
              </div>
            </div>
          </section>

          {data.regime === "defensive" && (
            <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              ⚠️ 시장이 200일선 아래입니다. 신규 매수를 권장하지 않고 기존 보유 종목은
              유지하세요. 시장 회복(SPY {">"} 200SMA) 시 다음 달 자동으로 신규 슬롯이
              채워집니다.
            </div>
          )}

          {/* 갱신 버튼 */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onRefresh(true)}
              disabled={refreshing}
              className="rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800"
            >
              {refreshing ? "처리 중…" : "🔍 미리보기 (DB 변경 없음)"}
            </button>
            <button
              type="button"
              onClick={() => onRefresh(false)}
              disabled={refreshing}
              className="rounded border border-emerald-600 bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {refreshing ? "처리 중…" : "🔄 재선정 (DB 갱신)"}
            </button>
            {data.last_refreshed_at && (
              <span className="text-xs text-zinc-500">
                마지막 갱신: {new Date(data.last_refreshed_at).toLocaleString("ko-KR")}
              </span>
            )}
          </div>
          {refreshNote && (
            <div className="rounded border border-zinc-200 bg-zinc-50 p-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
              {refreshNote}
            </div>
          )}

          {/* 메인 테이블 — Active picks */}
          {activePicks.length === 0 ? (
            <div className="rounded border border-zinc-200 bg-zinc-50 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900">
              현재 추천 종목이 없습니다. 위 "재선정" 버튼으로 산출하세요.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 dark:bg-zinc-900">
                  <tr className="text-left">
                    <th className="px-3 py-2">순위</th>
                    <th className="px-3 py-2">티커</th>
                    <th className="px-3 py-2">상태</th>
                    <th className="px-3 py-2">권장 행동</th>
                    <th className="px-3 py-2 text-right">비중</th>
                    <th className="px-3 py-2 text-right">종합 점수</th>
                    <th className="px-3 py-2">게이트</th>
                    <th className="px-3 py-2 text-right">RS</th>
                    <th className="px-3 py-2 text-right">12개월</th>
                    <th className="px-3 py-2 text-right">200일 거리</th>
                  </tr>
                </thead>
                <tbody>
                  {activePicks.map((p) => {
                    const brk = p.score_breakdown ?? {};
                    return (
                      <tr
                        key={p.id}
                        className="border-t border-zinc-200 dark:border-zinc-800"
                      >
                        <td className="px-3 py-2 font-mono">
                          {p.rank}
                        </td>
                        <td className="px-3 py-2 font-semibold">{p.symbol}</td>
                        <td className="px-3 py-2">
                          <span
                            className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLOR[p.status]}`}
                          >
                            {STATUS_LABEL[p.status]}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className={`rounded px-2 py-0.5 text-xs font-bold ${ACTION_COLOR[p.fidelity_action]}`}
                          >
                            {ACTION_LABEL[p.fidelity_action]}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {parseFloat(p.weight_pct).toFixed(1)}%
                        </td>
                        <td className="px-3 py-2 text-right font-mono font-semibold">
                          {fmtScore(p.composite_score)}
                        </td>
                        <td className="px-3 py-2">
                          <GateBadges gates={p.gate_results} />
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {brk.rs_pct ?? "—"}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {fmtPct(brk.mom_12mo as number)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {fmtPct(brk.sma200_dist as number)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {exitedPicks.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-lg font-semibold text-rose-700 dark:text-rose-300">
                🗑 청산 권고 ({exitedPicks.length})
              </h2>
              <div className="overflow-x-auto rounded-lg border border-rose-200 dark:border-rose-900">
                <table className="w-full text-sm">
                  <thead className="bg-rose-50 dark:bg-rose-950/40">
                    <tr className="text-left">
                      <th className="px-3 py-2">티커</th>
                      <th className="px-3 py-2">상태</th>
                      <th className="px-3 py-2">권장</th>
                    </tr>
                  </thead>
                  <tbody>
                    {exitedPicks.map((p) => (
                      <tr
                        key={p.id}
                        className="border-t border-rose-200 dark:border-rose-900"
                      >
                        <td className="px-3 py-2 font-semibold">{p.symbol}</td>
                        <td className="px-3 py-2">
                          <span
                            className={`rounded px-2 py-0.5 text-xs ${STATUS_COLOR[p.status]}`}
                          >
                            {STATUS_LABEL[p.status]}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className={`rounded px-2 py-0.5 text-xs font-bold ${ACTION_COLOR[p.fidelity_action]}`}
                          >
                            {ACTION_LABEL[p.fidelity_action]}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <footer className="text-xs text-zinc-500">
            <p>
              📌 게이트 4개 모두 통과 + 종합 점수 상위 10종목을 매월 균등 가중(10%)으로
              제안합니다. 직전 월 보유 중 상위 6개는 자동 유지, 신규 4 슬롯만 교체합니다.
              백테스트(2018-2024 S&P 500): Sharpe 1.21 · 알파 +21.6%/년 · MDD -29%.
            </p>
          </footer>
        </>
      )}
    </main>
  );
}
