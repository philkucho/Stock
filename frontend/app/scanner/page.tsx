"use client";

import { useEffect, useMemo, useState } from "react";
import HelpDrawer from "./HelpDrawer";
import TopNav from "@/components/TopNav";

import {
  fetchScannerDiagnostics,
  fetchScannerToday,
  fetchWhitelist,
  type Diagnostics,
  type ScannerCandidate,
  type ScannerToday,
  type WhitelistResponse,
} from "@/lib/api";

const SIGNAL_LABEL: Record<string, string> = {
  volume_trend: "거래량 추세",
  ma_alignment: "이동평균 정배열",
  rsi_bullish: "RSI 상승 구간",
  macd: "MACD 전환",
  above_ma200: "200일선 위",
  breakout_20d: "20일 신고가",
};

const SIGNAL_SHORT: Record<string, string> = {
  volume_trend: "VT",
  ma_alignment: "MA",
  rsi_bullish: "RSI",
  macd: "MAC",
  above_ma200: "M200",
  breakout_20d: "BRK",
};

const PHASE_LABEL: Record<string, { ko: string; cls: string; emoji: string; tip: string }> = {
  pre: {
    ko: "실적 임박",
    cls: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
    emoji: "🚨",
    tip: "5일 이내 실적 발표 예정 — 진입 비추천 (손익 변동 위험)",
  },
  post: {
    ko: "실적 직후",
    cls: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
    emoji: "📈",
    tip: "최근 실적 발표 후 — PEAD 알파 +2.56% 표본",
  },
  clean: {
    ko: "재료 없음",
    cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    emoji: "✅",
    tip: "실적 영향 없음 — 순수 모멘텀 알파 +1.41% 표본",
  },
};

function scoreColor(score: number): string {
  if (score >= 5) return "bg-emerald-500 text-white";
  if (score >= 4) return "bg-emerald-400 text-white";
  if (score >= 3) return "bg-blue-400 text-white";
  if (score >= 2) return "bg-zinc-400 text-white";
  return "bg-zinc-300 text-zinc-700";
}

function fmtSignalCell(name: string, val: number): JSX.Element {
  if (val > 0) return <span className="text-emerald-600 font-semibold">+{val}</span>;
  if (val < 0) return <span className="text-rose-600 font-semibold">{val}</span>;
  return <span className="text-zinc-400">·</span>;
}

function CandidateRow({ c }: { c: ScannerCandidate }) {
  const phase = PHASE_LABEL[c.earnings_phase] ?? PHASE_LABEL.clean;
  const hist = c.historical;
  return (
    <tr className="border-b border-zinc-200 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
      <td className="px-3 py-2 font-mono text-zinc-500">{c.rank}</td>
      <td className="px-3 py-2">
        <div className="font-bold text-base">{c.symbol}</div>
        {c.sector && <div className="text-xs text-zinc-500">{c.sector}</div>}
      </td>
      <td className="px-3 py-2">
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs ${phase.cls}`}
          title={phase.tip}
        >
          {phase.emoji} {phase.ko}
        </span>
        {c.earnings_next && (
          <div className="text-[10px] text-zinc-500 mt-0.5">
            {c.earnings_next}{" "}
            {c.earnings_days !== null && (
              <span className="font-mono">
                ({c.earnings_days >= 0 ? "+" : ""}
                {c.earnings_days}일)
              </span>
            )}
          </div>
        )}
      </td>
      <td className="px-3 py-2 text-right font-mono">${c.close.toFixed(2)}</td>
      <td className="px-3 py-2 text-right">
        {c.vol_vs_20d_avg !== null && (
          <span
            className={`font-mono ${c.vol_vs_20d_avg >= 1.5 ? "text-emerald-600 font-bold" : "text-zinc-600"}`}
          >
            {c.vol_vs_20d_avg.toFixed(2)}×
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-center">
        <span
          className={`inline-flex items-center justify-center w-9 h-7 rounded-md font-bold ${scoreColor(c.total_score)}`}
        >
          {c.total_score}
        </span>
      </td>
      <td className="px-3 py-2">
        <div className="flex gap-1.5 font-mono text-sm">
          {Object.entries(SIGNAL_SHORT).map(([key, short]) => (
            <span
              key={key}
              className="inline-block w-9 text-center"
              title={`${SIGNAL_LABEL[key]}: ${c.signals[key] ?? 0}`}
            >
              {fmtSignalCell(short, c.signals[key] ?? 0)}
            </span>
          ))}
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        {hist ? (
          <div className="text-xs">
            <div className="font-mono">
              <span className={hist.hit_rate >= 0.7 ? "font-bold text-emerald-600" : ""}>
                {(hist.hit_rate * 100).toFixed(0)}%
              </span>{" "}
              hit
            </div>
            <div className="font-mono text-zinc-500">
              평균{" "}
              <span className={hist.avg_ret > 0.02 ? "font-bold text-emerald-600" : ""}>
                {hist.avg_ret >= 0 ? "+" : ""}
                {(hist.avg_ret * 100).toFixed(2)}%
              </span>{" "}
              <span className="text-[10px]">(n={hist.n})</span>
            </div>
          </div>
        ) : (
          <span className="text-zinc-400 text-xs">—</span>
        )}
      </td>
    </tr>
  );
}

export default function ScannerPage() {
  const [data, setData] = useState<ScannerToday | null>(null);
  const [whitelist, setWhitelist] = useState<WhitelistResponse | null>(null);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // 필터
  const [scoreMin, setScoreMin] = useState(2);
  const [earningsMode, setEarningsMode] = useState<"pre_only" | "exclude" | "off">("pre_only");
  const [phaseFilter, setPhaseFilter] = useState<"all" | "clean" | "post">("all");
  const [sectorFilter, setSectorFilter] = useState<string>("all");
  const [helpOpen, setHelpOpen] = useState(false);

  // ESC로 도움말 닫기
  useEffect(() => {
    if (!helpOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHelpOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [helpOpen]);

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const [today, wl, dg] = await Promise.all([
        fetchScannerToday({ scoreMin: 1, earningsMode, top: 100 }),
        fetchWhitelist(),
        fetchScannerDiagnostics(),
      ]);
      setData(today);
      setWhitelist(wl);
      setDiag(dg);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [earningsMode]);

  const filtered = useMemo(() => {
    if (!data) return [];
    return data.candidates.filter((c) => {
      if (c.total_score < scoreMin) return false;
      if (phaseFilter !== "all" && c.earnings_phase !== phaseFilter) return false;
      if (sectorFilter !== "all" && c.sector !== sectorFilter) return false;
      return true;
    });
  }, [data, scoreMin, phaseFilter, sectorFilter]);

  const sectors = useMemo(() => {
    if (!data) return [];
    const s = new Set<string>();
    data.candidates.forEach((c) => c.sector && s.add(c.sector));
    return Array.from(s).sort();
  }, [data]);

  return (
    <main className="min-h-full px-6 py-8 max-w-7xl mx-auto">
      <div className="mb-4">
        <TopNav />
      </div>
      <header className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-2xl font-bold">📊 오늘의 종목 스캐너</h1>
          <div className="flex items-center gap-3">
            <button
              onClick={() => setHelpOpen(!helpOpen)}
              className={`text-sm rounded-lg border px-3 py-1.5 transition-colors ${
                helpOpen
                  ? "border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-400 dark:bg-blue-950 dark:text-blue-300"
                  : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
              }`}
              title="화면 항목·시그널·점수 해석 (ESC로 닫기)"
            >
              ❓ 도움말
            </button>
          </div>
        </div>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          v3 화이트리스트 122종목 + 시장 상태 + 실적 분류 (PEAD 인지)
        </p>
      </header>

      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />

      {/* 시장 상태 */}
      {data && (
        <section className="mb-6 grid grid-cols-1 md:grid-cols-4 gap-3">
          <div
            className={`rounded-lg p-4 border-2 ${
              data.regime.on
                ? "bg-emerald-50 dark:bg-emerald-950/30 border-emerald-300 dark:border-emerald-800"
                : "bg-rose-50 dark:bg-rose-950/30 border-rose-300 dark:border-rose-800"
            }`}
          >
            <div className="text-xs text-zinc-500 mb-1">시장 상태</div>
            <div className="text-lg font-bold">
              {data.regime.on ? "🟢 진입 가능" : "🛑 진입 차단"}
            </div>
            <div className="text-xs text-zinc-600 dark:text-zinc-400 mt-1">
              S&P{" "}
              <span
                className={data.regime.spy_above_ma ? "text-emerald-600" : "text-rose-600"}
              >
                {data.regime.spy_above_ma ? "200일선 위" : "200일선 아래"}
              </span>
              {data.regime.vix_close !== null && (
                <>
                  {" · 변동성 "}
                  <span
                    className={
                      data.regime.vix_close < data.regime.vix_threshold
                        ? "text-emerald-600"
                        : "text-rose-600"
                    }
                  >
                    {data.regime.vix_close.toFixed(1)}
                  </span>
                </>
              )}
            </div>
          </div>

          <div className="rounded-lg p-4 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800">
            <div className="text-xs text-zinc-500 mb-1">기준 일자</div>
            <div className="text-lg font-bold">{data.as_of}</div>
            <div className="text-xs text-zinc-500 mt-1">
              {whitelist ? `화이트리스트 ${whitelist.n_whitelist}종목` : ""}
            </div>
          </div>

          <div className="rounded-lg p-4 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800">
            <div className="text-xs text-zinc-500 mb-1">실적 분류</div>
            <div className="text-sm">
              <span className="inline-block px-1.5 py-0.5 rounded bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300 mr-2">
                🚨 임박 {data.n_pre_blackout}
              </span>
              <span className="inline-block px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 mr-2">
                📈 직후 {data.n_post_pead}
              </span>
            </div>
            <div className="text-sm mt-1">
              <span className="inline-block px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                ✅ 재료없음 {data.n_clean}
              </span>
            </div>
          </div>

          <div className="rounded-lg p-4 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800">
            <div className="text-xs text-zinc-500 mb-1">데이터 현황</div>
            <div className="text-sm">
              {diag && (
                <>
                  <div>일봉: {diag.interval_1d.n_symbols}종목</div>
                  <div className="text-xs text-zinc-500">
                    실적 캘린더: {diag.earnings_calendar.n_symbols}종목
                  </div>
                </>
              )}
            </div>
          </div>
        </section>
      )}

      {/* 필터 */}
      <section className="mb-4 p-4 rounded-lg bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800">
        <div className="flex flex-wrap gap-4 items-center text-sm">
          <div>
            <label className="block text-xs text-zinc-500 mb-1">최소 점수</label>
            <select
              value={scoreMin}
              onChange={(e) => setScoreMin(Number(e.target.value))}
              className="rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1"
            >
              {[1, 2, 3, 4, 5].map((s) => (
                <option key={s} value={s}>
                  {s} 이상
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs text-zinc-500 mb-1">실적 분류 모드</label>
            <select
              value={earningsMode}
              onChange={(e) => setEarningsMode(e.target.value as "pre_only" | "exclude" | "off")}
              className="rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1"
            >
              <option value="pre_only">실적 임박만 차단 (PEAD 허용, 권장)</option>
              <option value="exclude">실적 ±5일 모두 차단 (보수적)</option>
              <option value="off">차단 없음</option>
            </select>
          </div>

          <div>
            <label className="block text-xs text-zinc-500 mb-1">실적 단계 필터</label>
            <select
              value={phaseFilter}
              onChange={(e) => setPhaseFilter(e.target.value as "all" | "clean" | "post")}
              className="rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1"
            >
              <option value="all">전체</option>
              <option value="clean">재료없음만</option>
              <option value="post">실적 직후 (PEAD)만</option>
            </select>
          </div>

          <div>
            <label className="block text-xs text-zinc-500 mb-1">섹터</label>
            <select
              value={sectorFilter}
              onChange={(e) => setSectorFilter(e.target.value)}
              className="rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1"
            >
              <option value="all">전체</option>
              {sectors.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>

          <button
            onClick={load}
            disabled={loading}
            className="ml-auto px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium disabled:opacity-50"
          >
            {loading ? "불러오는 중..." : "🔄 새로고침"}
          </button>
        </div>
        <div className="text-xs text-zinc-500 mt-2">
          {data
            ? `${data.candidates.length}개 후보 중 ${filtered.length}개 표시 (필터 적용 후)`
            : ""}
        </div>
      </section>

      {/* 에러 */}
      {err && (
        <div className="mb-4 p-4 rounded-lg bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-300">
          오류: {err}
        </div>
      )}

      {/* 후보 테이블 */}
      <section className="rounded-lg border border-zinc-200 dark:border-zinc-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-100 dark:bg-zinc-900">
            <tr className="text-left">
              <th className="px-3 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">종목</th>
              <th className="px-3 py-2 font-medium">실적</th>
              <th className="px-3 py-2 font-medium text-right">종가</th>
              <th className="px-3 py-2 font-medium text-right" title="20일 평균 대비 거래량">
                거래량
              </th>
              <th className="px-3 py-2 font-medium text-center">점수</th>
              <th className="px-3 py-2 font-medium" title="VT=거래량추세, MA=정배열, RSI=상승구간, MAC=MACD, M200=200일선, BRK=20일고가">
                시그널
              </th>
              <th className="px-3 py-2 font-medium text-right">과거 통계</th>
            </tr>
          </thead>
          <tbody>
            {loading && !data && (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-zinc-500">
                  데이터 불러오는 중...
                </td>
              </tr>
            )}
            {!loading && filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-zinc-500">
                  조건에 맞는 후보 없음
                </td>
              </tr>
            )}
            {filtered.map((c) => (
              <CandidateRow key={c.symbol} c={c} />
            ))}
          </tbody>
        </table>
      </section>

      {/* 빠른 시그널 참조 — 풀 설명은 우상단 ❓ 도움말 드로어에서 */}
      <section className="mt-6 rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/50 dark:text-zinc-400">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-semibold text-zinc-900 dark:text-zinc-100">시그널 빠른 참조:</span>
          <span><strong className="font-mono">VT</strong> 거래량추세</span>
          <span>·</span>
          <span><strong className="font-mono">MA</strong> 정배열</span>
          <span>·</span>
          <span><strong className="font-mono">RSI</strong> 55~70 상승</span>
          <span>·</span>
          <span><strong className="font-mono">MAC</strong> MACD전환</span>
          <span>·</span>
          <span><strong className="font-mono">M200</strong> 200일선</span>
          <span>·</span>
          <span><strong className="font-mono">BRK</strong> 20일고가</span>
          <button
            onClick={() => setHelpOpen(true)}
            className="ml-auto text-blue-600 hover:underline dark:text-blue-400"
          >
            ❓ 상세 도움말 →
          </button>
        </div>
      </section>
    </main>
  );
}
