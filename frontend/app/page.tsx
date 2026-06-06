"use client";

import { useCallback, useEffect, useState } from "react";
import TopNav from "@/components/TopNav";
import {
  fetchAccount,
  fetchHealth,
  fetchPositions,
  type Account,
  type Position,
} from "@/lib/api";
import HelpDrawer from "./HelpDrawer";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; version: string }
  | { kind: "error"; message: string };

const REFRESH_MS = 60_000;

function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtSignedUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${fmtUsd(v)}`;
}

function fmtSignedPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** 손익 색상 — 수익 녹색 / 손실 빨강 / 0 중립 */
function pnlColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0)
    return "text-zinc-700 dark:text-zinc-300";
  return v > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-red-600 dark:text-red-400";
}

function SummaryCard({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: { text: string; className: string };
  valueClass?: string;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs text-zinc-500">{label}</div>
      <div
        className={`mt-1 font-mono text-xl font-semibold ${
          valueClass ?? "text-black dark:text-zinc-50"
        }`}
      >
        {value}
      </div>
      {sub && <div className={`mt-0.5 text-xs ${sub.className}`}>{sub.text}</div>}
    </div>
  );
}

export default function Home() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });
  const [account, setAccount] = useState<Account | null>(null);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [positions, setPositions] = useState<Position[] | null>(null);
  const [positionsError, setPositionsError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    const [acc, pos] = await Promise.allSettled([fetchAccount(), fetchPositions()]);
    if (acc.status === "fulfilled") {
      setAccount(acc.value);
      setAccountError(null);
    } else {
      setAccountError(String(acc.reason));
    }
    if (pos.status === "fulfilled") {
      setPositions(pos.value);
      setPositionsError(null);
    } else {
      setPositionsError(String(pos.reason));
    }
    setUpdatedAt(new Date());
    setRefreshing(false);
  }, []);

  useEffect(() => {
    fetchHealth()
      .then((h) => setHealth({ kind: "ok", version: h.version }))
      .catch((e) => setHealth({ kind: "error", message: String(e) }));

    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  // 일일 손익 = 자산총액 - 전일 자산총액
  const dailyPnlUsd =
    account?.equity != null && account?.last_equity != null
      ? account.equity - account.last_equity
      : null;

  // 미실현 손익 합계 (보유 포지션)
  const totalUnrealized =
    positions && positions.length > 0
      ? positions.reduce((sum, p) => sum + (Number(p.realized_pnl) || 0), 0)
      : positions
        ? 0
        : null;
  const totalMarketValue =
    positions && positions.length > 0
      ? positions.reduce((sum, p) => sum + (p.market_value ?? 0), 0)
      : 0;

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-5xl space-y-6">
        <TopNav />
        <header className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              Stock Autotrader
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              계좌 현황 대시보드 —{" "}
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                현재 모의투자(paper) 단계
              </span>
            </p>
          </div>
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
        </header>

        {/* 거래 차단 경고 — 위험 정보 시각 강조 */}
        {account?.trading_blocked && (
          <div className="rounded-lg border-2 border-red-500 bg-red-50 p-4 text-sm font-semibold text-red-700 dark:bg-red-950 dark:text-red-300">
            ⚠ 위험: 계좌 거래가 차단된 상태입니다 (trading_blocked). 브로커
            계정을 확인하세요.
          </div>
        )}

        {/* 계좌 요약 카드 */}
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-black dark:text-zinc-50">
              계좌 요약
              {account && (
                <span className="ml-2 text-xs font-normal text-zinc-500">
                  Alpaca {account.account_id} · {account.status}
                </span>
              )}
            </h2>
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              {updatedAt && (
                <span>
                  마지막 갱신{" "}
                  {updatedAt.toLocaleTimeString("ko-KR", { hour12: false })}
                </span>
              )}
              <button
                onClick={load}
                disabled={refreshing}
                className="rounded-md border border-zinc-300 px-2 py-1 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
              >
                {refreshing ? "갱신 중" : "↻ 갱신"}
              </button>
            </div>
          </div>

          {accountError && (
            <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
              ✗ 계좌 정보를 불러오지 못했습니다 — {accountError}
            </div>
          )}
          {!account && !accountError && (
            <p className="text-sm text-zinc-500">계좌 정보 불러오는 중</p>
          )}
          {account && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <SummaryCard
                label="자산총액 (현금 + 주식 평가액)"
                value={fmtUsd(account.equity)}
                sub={{
                  text: `오늘 손익 ${fmtSignedUsd(dailyPnlUsd)} (${fmtSignedPct(account.daily_pnl_pct)})`,
                  className: pnlColor(dailyPnlUsd),
                }}
              />
              <SummaryCard
                label="보유 현금"
                value={fmtUsd(account.balance_usd)}
              />
              <SummaryCard
                label="주식 평가금액"
                value={fmtUsd(totalMarketValue)}
                sub={{
                  text: `미실현 손익 ${fmtSignedUsd(totalUnrealized)}`,
                  className: pnlColor(totalUnrealized),
                }}
              />
              <SummaryCard
                label="매수가능금액"
                value={fmtUsd(account.buying_power)}
              />
            </div>
          )}
        </section>

        {/* 보유 종목 현황 */}
        <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-3 text-lg font-semibold text-black dark:text-zinc-50">
            보유 종목 현황
            {positions && positions.length > 0 && (
              <span className="ml-2 text-xs font-normal text-zinc-500">
                {positions.length}종목
              </span>
            )}
          </h2>
          {positionsError && (
            <p className="text-sm text-red-600 dark:text-red-400">
              ✗ 보유 종목을 불러오지 못했습니다 — {positionsError}
            </p>
          )}
          {!positions && !positionsError && (
            <p className="text-sm text-zinc-500">보유 종목 불러오는 중</p>
          )}
          {positions && positions.length === 0 && (
            <p className="text-sm text-zinc-500">
              보유 중인 종목이 없습니다. 다음 자동매매(평일 09:30 ET) 또는 AI
              자문 승인 시 진입됩니다.
            </p>
          )}
          {positions && positions.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-200 text-left text-xs text-zinc-500 dark:border-zinc-800">
                    <th className="py-2 pr-3">종목</th>
                    <th className="py-2 pr-3 text-right">수량</th>
                    <th className="py-2 pr-3 text-right">평균단가</th>
                    <th className="py-2 pr-3 text-right">현재가</th>
                    <th className="py-2 pr-3 text-right">평가금액</th>
                    <th className="py-2 pr-3 text-right">미실현 손익</th>
                    <th className="py-2 text-right">수익률</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => {
                    const pnl = Number(p.realized_pnl) || 0;
                    return (
                      <tr
                        key={p.symbol}
                        className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/50"
                      >
                        <td className="py-2.5 pr-3 font-mono font-semibold text-black dark:text-zinc-50">
                          {p.symbol}
                        </td>
                        <td className="py-2.5 pr-3 text-right font-mono text-zinc-700 dark:text-zinc-300">
                          {Number(p.quantity).toLocaleString()}주
                        </td>
                        <td className="py-2.5 pr-3 text-right font-mono text-zinc-700 dark:text-zinc-300">
                          {fmtUsd(Number(p.avg_price))}
                        </td>
                        <td className="py-2.5 pr-3 text-right font-mono text-zinc-700 dark:text-zinc-300">
                          {fmtUsd(p.current_price)}
                        </td>
                        <td className="py-2.5 pr-3 text-right font-mono text-zinc-700 dark:text-zinc-300">
                          {fmtUsd(p.market_value)}
                        </td>
                        <td
                          className={`py-2.5 pr-3 text-right font-mono font-semibold ${pnlColor(pnl)}`}
                        >
                          {fmtSignedUsd(pnl)}
                        </td>
                        <td
                          className={`py-2.5 text-right font-mono font-semibold ${pnlColor(p.unrealized_pl_pct)}`}
                        >
                          {fmtSignedPct(p.unrealized_pl_pct)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 백엔드 상태 — 컴팩트 */}
        <section className="rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
          {health.kind === "loading" && (
            <p className="text-zinc-500">API 서버 연결 확인 중</p>
          )}
          {health.kind === "ok" && (
            <p className="text-emerald-600 dark:text-emerald-400">
              ✓ API 서버 정상 (v{health.version})
            </p>
          )}
          {health.kind === "error" && (
            <div className="text-red-600 dark:text-red-400">
              ✗ API 서버 응답 없음 — {health.message}
              <details className="mt-1 text-xs text-zinc-500">
                <summary className="cursor-pointer">관리자용</summary>
                서버 시작:{" "}
                <code>uvicorn api.main:app --reload --port 8000</code>
              </details>
            </div>
          )}
        </section>

        <footer className="pt-2 text-xs text-zinc-500">
          현재 모의투자(paper) 단계 — Alpaca paper 계좌 기준. 데이터는 1분마다
          자동 갱신됩니다.
        </footer>
      </div>
    </main>
  );
}
