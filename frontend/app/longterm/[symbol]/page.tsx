"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import TopNav from "@/components/TopNav";
import {
  fetchLongtermDetail,
  type ChecklistItem,
  type LongtermDetail,
  type SeriesPoint,
} from "@/lib/api";

// ── 헬퍼 ──────────────────────────────────────────────────────────
const STATUS_BG: Record<ChecklistItem["status"], string> = {
  green: "border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40",
  yellow: "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40",
  red: "border-rose-300 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/40",
  unknown: "border-zinc-300 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900",
};

const STATUS_DOT: Record<ChecklistItem["status"], string> = {
  green: "bg-emerald-600",
  yellow: "bg-amber-500",
  red: "bg-rose-600",
  unknown: "bg-zinc-400",
};

const STATUS_LABEL: Record<ChecklistItem["status"], string> = {
  green: "안전",
  yellow: "주의",
  red: "위험",
  unknown: "—",
};

function fmtUSD(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return `$${v.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function fmtBillions(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${(v / 1e9).toFixed(2)}B`;
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${v.toFixed(digits)}%`;
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(digits);
}

/** 시리즈를 차트용으로 변환 (최신순 → 오래된순) + 단위 변환. */
function prepBillions(s: SeriesPoint[]): { date: string; value: number }[] {
  return [...s]
    .filter((p) => p.value !== null)
    .reverse()
    .map((p) => ({ date: p.date.slice(0, 7), value: (p.value as number) / 1e9 }));
}

function prepRaw(s: SeriesPoint[]): { date: string; value: number }[] {
  return [...s]
    .filter((p) => p.value !== null)
    .reverse()
    .map((p) => ({ date: p.date.slice(0, 7), value: p.value as number }));
}

// ── 메인 페이지 ──────────────────────────────────────────────────
export default function LongtermDetailPage() {
  const params = useParams();
  const symbol = (params?.symbol as string)?.toUpperCase() ?? "";

  const [data, setData] = useState<LongtermDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    fetchLongtermDetail(symbol)
      .then(setData)
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [symbol]);

  return (
    <main className="mx-auto max-w-7xl space-y-6 p-6">
      <TopNav />

      <div className="flex items-center gap-3">
        <Link
          href="/longterm"
          className="text-sm text-zinc-500 hover:underline"
        >
          ← 중장기 picks 돌아가기
        </Link>
      </div>

      {loading && <div className="text-sm text-zinc-500">불러오는 중…</div>}
      {error && (
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
          오류: {error}
        </div>
      )}

      {data && (
        <>
          {/* 헤더 */}
          <header className="space-y-2">
            <div className="flex items-baseline gap-3">
              <h1 className="text-3xl font-bold">{data.symbol}</h1>
              <span className="text-xl text-zinc-600 dark:text-zinc-400">
                {fmtUSD(data.snapshot.current_price)}
              </span>
              {data.snapshot.high_52w_dist_pct !== null && (
                <span className="text-sm text-zinc-500">
                  52주 고가 대비 {fmtPct(data.snapshot.high_52w_dist_pct)}
                </span>
              )}
            </div>
            <div className="text-sm text-zinc-500">
              {data.snapshot.sector} · {data.snapshot.industry}
              {data.snapshot.next_earnings_date && (
                <>
                  {" · "}
                  다음 어닝{" "}
                  <span className="font-medium">
                    {data.snapshot.next_earnings_date}
                  </span>
                  {data.snapshot.days_to_earnings !== null && (
                    <> ({data.snapshot.days_to_earnings}일 남음)</>
                  )}
                </>
              )}
            </div>
            {data.snapshot.long_business_summary && (
              <p className="max-w-3xl text-sm text-zinc-600 dark:text-zinc-400">
                {data.snapshot.long_business_summary}
              </p>
            )}
          </header>

          {/* 투자 체크리스트 */}
          <section className="space-y-2">
            <h2 className="text-lg font-semibold">🩺 투자 체크리스트</h2>
            <p className="text-xs text-zinc-500">
              게이트 통과는 최소 자격일 뿐. 이 8개 지표가 진짜 매수 적정성을
              가릅니다.
            </p>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
              {data.checklist.map((c) => (
                <div
                  key={c.key}
                  className={`rounded-lg border p-3 ${STATUS_BG[c.status]}`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                      {c.label}
                    </span>
                    <span className="flex items-center gap-1 text-[10px]">
                      <span
                        className={`h-2 w-2 rounded-full ${STATUS_DOT[c.status]}`}
                      />
                      {STATUS_LABEL[c.status]}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-lg font-bold">
                    {c.value !== null ? c.value : "—"}
                  </div>
                  <div className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">
                    {c.comment}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* 매출 + 영업이익 분기 추이 */}
          <section className="space-y-2">
            <h2 className="text-lg font-semibold">📈 분기 매출 + 영업이익 추이</h2>
            <div className="h-72 w-full rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={prepBillions(data.series.quarterly_revenue).map(
                    (p, i) => {
                      const op = prepBillions(
                        data.series.quarterly_operating_income,
                      )[i];
                      const ni = prepBillions(
                        data.series.quarterly_net_income,
                      )[i];
                      return {
                        date: p.date,
                        매출: p.value,
                        영업이익: op?.value ?? null,
                        순이익: ni?.value ?? null,
                      };
                    },
                  )}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" />
                  <YAxis tickFormatter={(v) => `${v.toFixed(1)}B`} />
                  <Tooltip
                    formatter={(v) => `${Number(v).toFixed(2)}B`}
                  />
                  <Legend />
                  <Bar dataKey="매출" fill="#2563eb" />
                  <Bar dataKey="영업이익" fill="#10b981" />
                  <Bar dataKey="순이익" fill="#a855f7" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
              <div className="rounded border border-zinc-200 p-2 dark:border-zinc-700">
                <div className="text-zinc-500">매출총이익률</div>
                <div className="font-mono font-semibold">
                  {fmtPct(data.margins_latest.gross)}
                </div>
              </div>
              <div className="rounded border border-zinc-200 p-2 dark:border-zinc-700">
                <div className="text-zinc-500">영업이익률</div>
                <div className="font-mono font-semibold">
                  {fmtPct(data.margins_latest.operating)}
                </div>
              </div>
              <div className="rounded border border-zinc-200 p-2 dark:border-zinc-700">
                <div className="text-zinc-500">순이익률</div>
                <div className="font-mono font-semibold">
                  {fmtPct(data.margins_latest.net)}
                </div>
              </div>
              <div className="rounded border border-zinc-200 p-2 dark:border-zinc-700">
                <div className="text-zinc-500">FCF 마진</div>
                <div className="font-mono font-semibold">
                  {fmtPct(data.margins_latest.fcf)}
                </div>
              </div>
            </div>
          </section>

          {/* EPS 분기 추이 */}
          {data.series.quarterly_eps.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-lg font-semibold">💰 분기 EPS 추이</h2>
              <div className="h-56 w-full rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={prepRaw(data.series.quarterly_eps)}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" />
                    <YAxis tickFormatter={(v) => `$${v.toFixed(2)}`} />
                    <Tooltip
                      formatter={(v) => `$${Number(v).toFixed(2)}`}
                    />
                    <Bar dataKey="value" fill="#0891b2" name="EPS" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}

          {/* 현금흐름 OCF + FCF */}
          {data.series.quarterly_ocf.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-lg font-semibold">💵 분기 현금흐름 (영업/자유)</h2>
              <div className="h-56 w-full rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={prepBillions(data.series.quarterly_ocf).map(
                      (p, i) => {
                        const fcf = prepBillions(
                          data.series.quarterly_fcf,
                        )[i];
                        return {
                          date: p.date,
                          영업현금흐름: p.value,
                          자유현금흐름: fcf?.value ?? null,
                        };
                      },
                    )}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" />
                    <YAxis tickFormatter={(v) => `${v.toFixed(1)}B`} />
                    <Tooltip formatter={(v) => `${Number(v).toFixed(2)}B`} />
                    <Legend />
                    <Bar dataKey="영업현금흐름" fill="#059669" />
                    <Bar dataKey="자유현금흐름" fill="#16a34a" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}

          {/* 주별 거래대금 + 종가 (52주) */}
          {(data.series.weekly_volume?.length ?? 0) > 0 && (
            <section className="space-y-2">
              <h2 className="text-lg font-semibold">
                📊 주별 거래대금 + 종가 (52주)
              </h2>
              <p className="text-xs text-zinc-500">
                거래대금 폭증 = 시장 관심도 ↑ · 가격 상승 + 거래대금 동반 상승은
                건강한 추세, 가격만 상승하고 거래대금 정체는 약세 신호.
              </p>
              <div className="h-72 w-full rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart
                    data={[...(data.series.weekly_volume ?? [])]
                      .reverse()
                      .map((w) => ({
                        date: w.date.slice(5),  // MM-DD
                        "거래대금($M)": w.dollar_volume_musd,
                        "종가($)": w.close,
                      }))}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" interval={3} />
                    <YAxis
                      yAxisId="left"
                      tickFormatter={(v) =>
                        v >= 1000 ? `${(v / 1000).toFixed(1)}B` : `${v.toFixed(0)}M`
                      }
                      label={{ value: "거래대금", angle: -90, position: "insideLeft", style: { fontSize: 11 } }}
                    />
                    <YAxis
                      yAxisId="right"
                      orientation="right"
                      tickFormatter={(v) => `$${v.toFixed(0)}`}
                      label={{ value: "종가", angle: 90, position: "insideRight", style: { fontSize: 11 } }}
                    />
                    <Tooltip
                      formatter={(v, name) => {
                        if (name === "거래대금($M)") {
                          const n = Number(v);
                          return n >= 1000
                            ? `${(n / 1000).toFixed(2)}B`
                            : `${n.toFixed(0)}M`;
                        }
                        return `$${Number(v).toFixed(2)}`;
                      }}
                    />
                    <Legend />
                    <Bar
                      yAxisId="left"
                      dataKey="거래대금($M)"
                      fill="#8b5cf6"
                      opacity={0.7}
                    />
                    <Line
                      yAxisId="right"
                      type="monotone"
                      dataKey="종가($)"
                      stroke="#dc2626"
                      strokeWidth={2}
                      dot={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}

          {/* 부채 + 자본 */}
          {data.series.quarterly_total_debt.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-lg font-semibold">🏦 분기 부채 vs 자기자본</h2>
              <div className="h-56 w-full rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={prepBillions(data.series.quarterly_total_debt).map(
                      (p, i) => {
                        const eq = prepBillions(data.series.quarterly_equity)[i];
                        return {
                          date: p.date,
                          총부채: p.value,
                          자기자본: eq?.value ?? null,
                        };
                      },
                    )}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" />
                    <YAxis tickFormatter={(v) => `${v.toFixed(1)}B`} />
                    <Tooltip formatter={(v) => `${Number(v).toFixed(2)}B`} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="총부채"
                      stroke="#dc2626"
                      strokeWidth={2}
                    />
                    <Line
                      type="monotone"
                      dataKey="자기자본"
                      stroke="#2563eb"
                      strokeWidth={2}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}

          {/* 밸류에이션 + 위험 표 */}
          <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
              <h3 className="mb-2 font-semibold">📊 밸류에이션</h3>
              <table className="w-full text-sm">
                <tbody>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">Forward P/E</td>
                    <td className="text-right font-mono">
                      {fmtNum(data.valuation.forward_pe, 1)}
                    </td>
                  </tr>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">EV / EBITDA</td>
                    <td className="text-right font-mono">
                      {fmtNum(data.valuation.ev_ebitda, 1)}
                    </td>
                  </tr>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">EV / Revenue</td>
                    <td className="text-right font-mono">
                      {fmtNum(data.valuation.ev_revenue, 1)}
                    </td>
                  </tr>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">P/B</td>
                    <td className="text-right font-mono">
                      {fmtNum(data.valuation.price_to_book, 2)}
                    </td>
                  </tr>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">P/S</td>
                    <td className="text-right font-mono">
                      {fmtNum(data.valuation.price_to_sales, 2)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
              <h3 className="mb-2 font-semibold">⚠️ 위험 지표</h3>
              <table className="w-full text-sm">
                <tbody>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">Beta</td>
                    <td className="text-right font-mono">
                      {fmtNum(data.risk.beta, 2)}
                    </td>
                  </tr>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">기관 보유 %</td>
                    <td className="text-right font-mono">
                      {data.risk.institutional_pct !== null
                        ? `${(data.risk.institutional_pct * 100).toFixed(1)}%`
                        : "—"}
                    </td>
                  </tr>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">인사이더 보유 %</td>
                    <td className="text-right font-mono">
                      {data.risk.insider_pct !== null
                        ? `${(data.risk.insider_pct * 100).toFixed(2)}%`
                        : "—"}
                    </td>
                  </tr>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">Short Ratio</td>
                    <td className="text-right font-mono">
                      {fmtNum(data.risk.short_ratio, 2)}
                    </td>
                  </tr>
                  <tr className="border-t border-zinc-200 dark:border-zinc-800">
                    <td className="py-1.5 text-zinc-500">52주 고가</td>
                    <td className="text-right font-mono">
                      {fmtUSD(data.snapshot.fifty_two_week_high)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <footer className="text-xs text-zinc-500">
            <p>
              데이터: yfinance ({data.fetched_at?.slice(0, 10)} 갱신).
              체크리스트 임계값은 일반적 가이드라인 — 종목/섹터별 정상 범위는
              다를 수 있습니다. 매수 결정 전 어닝 리스크 (다음 어닝까지 7일
              이내), 52주 고가 거리 (-5% 이내), 베타 (시장 충격 대비) 우선 확인.
            </p>
          </footer>
        </>
      )}
    </main>
  );
}
