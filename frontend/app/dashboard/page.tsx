"use client";

import { useEffect, useMemo, useState } from "react";

import TopNav from "@/components/TopNav";
import {
  fetchDashboardToday,
  type DashboardCandidate,
  type DashboardResponse,
} from "@/lib/api";
import ChartModal from "./ChartModal";
import HelpDrawer from "./HelpDrawer";

const TIER_META: Record<string, { label: string; sub: string; cls: string; ring: string }> = {
  S: {
    label: "🥇 Tier S",
    sub: "최고 신뢰도 — 시그널 정렬 + 백테스트 검증 + 진입 안전",
    cls: "bg-emerald-50 dark:bg-emerald-950/30",
    ring: "border-emerald-400 dark:border-emerald-700",
  },
  A: {
    label: "🥈 Tier A",
    sub: "강한 후보 — 시그널 또는 PEAD 알파",
    cls: "bg-blue-50 dark:bg-blue-950/30",
    ring: "border-blue-400 dark:border-blue-700",
  },
  B: {
    label: "🥉 Tier B",
    sub: "보조 후보 — WHITELIST 검증 + 안전 진입",
    cls: "bg-amber-50 dark:bg-amber-950/30",
    ring: "border-amber-400 dark:border-amber-700",
  },
  C: {
    label: "👀 Tier C (관찰)",
    sub: "Watch — 시그널 약하거나 검증 부족",
    cls: "bg-zinc-50 dark:bg-zinc-900/50",
    ring: "border-zinc-300 dark:border-zinc-700",
  },
};

const PHASE_META: Record<string, { label: string; cls: string; emoji: string }> = {
  pre: { label: "실적 임박", emoji: "🚨", cls: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300" },
  post: { label: "실적 직후 (PEAD)", emoji: "📈", cls: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  clean: { label: "재료 없음", emoji: "✅", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" },
};

// Tier 통과 경로 — 어느 룰로 분류되었는지 사용자에게 노출
const PATH_META: Record<string, { label: string; tip: string; cls: string }> = {
  perfect: {
    label: "완벽 정렬",
    tip: "Score 6 — 모든 시그널 동시 점화 (가장 강력)",
    cls: "bg-emerald-200 text-emerald-900 dark:bg-emerald-900/60 dark:text-emerald-200",
  },
  stats: {
    label: "통계 강도",
    tip: "Score 5+ + 과거 hit≥65% AND 평균≥1.5% AND 표본≥10",
    cls: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  },
  battle: {
    label: "표본 압도",
    tip: "Score 5+ + 과거 hit≥70% AND 표본≥20 (충분히 검증)",
    cls: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/40 dark:text-cyan-300",
  },
  "score+stats": {
    label: "점수+검증",
    tip: "Score 4+ + 과거 hit≥55% + 표본≥8",
    cls: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  },
  "post-pead": {
    label: "PEAD 알파",
    tip: "Score 4+ + 실적 직후 (PEAD 효과로 백테스트 +2.56% / 5d 표본)",
    cls: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  },
  "high-score": {
    label: "고점수",
    tip: "Score 5+ but 통계 검증 부족 (표본 적거나 hit 약함)",
    cls: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
  },
  "wl-clean": {
    label: "화이트리스트",
    tip: "Score 3+ + 과거 hit≥55% + 실적 안전",
    cls: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  },
  "wl-pead": {
    label: "WL+PEAD",
    tip: "Score 3+ + 화이트리스트 + 실적 직후",
    cls: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  },
  watch: {
    label: "관찰",
    tip: "신호 약하거나 검증 부족 — 진입 비추천",
    cls: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-400",
  },
};

const POLARITY_CLS: Record<string, string> = {
  positive: "text-emerald-600 dark:text-emerald-400",
  negative: "text-rose-600 dark:text-rose-400",
  neutral: "text-zinc-600 dark:text-zinc-400",
};

const POLARITY_EMOJI: Record<string, string> = {
  positive: "▲",
  negative: "▼",
  neutral: "•",
};

function ScoreBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 text-zinc-500 shrink-0">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-zinc-200 dark:bg-zinc-800 overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-12 text-right font-mono text-zinc-600 dark:text-zinc-400">
        {value}/{max}
      </span>
    </div>
  );
}

function CandidateCard({ c, onChartClick }: { c: DashboardCandidate; onChartClick?: (symbol: string) => void }) {
  const phase = PHASE_META[c.earnings_phase];
  const tier = TIER_META[c.tier];
  const path = PATH_META[c.tier_path] ?? PATH_META.watch;
  const lvl = c.levels;
  const hist = c.historical;

  return (
    <div className={`rounded-xl border-2 p-5 ${tier.cls} ${tier.ring}`}>
      {/* 헤더 */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-xs font-mono text-zinc-500">#{c.rank}</span>
            <h3 className="text-2xl font-bold tracking-tight">{c.symbol}</h3>
            <span className="px-2 py-0.5 rounded-md text-xs font-bold bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900">
              {c.tier}
            </span>
            <span
              className={`px-2 py-0.5 rounded-md text-[11px] font-medium ${path.cls}`}
              title={path.tip}
            >
              {path.label}
            </span>
          </div>
          <div className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400 flex-wrap">
            {c.sector && <span>{c.sector}</span>}
            <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${phase.cls}`}>
              {phase.emoji} {phase.label}
            </span>
            {c.earnings_next && c.earnings_days !== null && (
              <span className="text-[10px] font-mono text-zinc-500">
                {c.earnings_next} ({c.earnings_days >= 0 ? "+" : ""}{c.earnings_days}일)
              </span>
            )}
          </div>
        </div>
        <div className="text-right">
          <div className="text-3xl font-bold font-mono">{c.total_score}</div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wider">시그널 점수</div>
        </div>
      </div>

      {/* 가격 + 진입 정보 */}
      {lvl ? (
        <div className="grid grid-cols-2 gap-2 mb-4 text-sm">
          <div className="rounded-lg bg-white dark:bg-zinc-900 p-3 border border-zinc-200 dark:border-zinc-800">
            <div className="text-xs text-zinc-500 mb-1">진입가</div>
            <div className="font-mono font-bold text-lg">${lvl.entry.toFixed(2)}</div>
            <div className="text-[10px] text-zinc-500 mt-1">
              평균거래량 대비{" "}
              <span className={c.vol_vs_20d_avg && c.vol_vs_20d_avg >= 1.5 ? "text-emerald-600 font-bold" : ""}>
                {c.vol_vs_20d_avg?.toFixed(2) ?? "—"}×
              </span>
            </div>
          </div>
          <div className="rounded-lg bg-white dark:bg-zinc-900 p-3 border border-zinc-200 dark:border-zinc-800">
            <div className="text-xs text-zinc-500 mb-1">손절가 (-{lvl.risk_pct.toFixed(2)}%)</div>
            <div className="font-mono font-bold text-lg text-rose-600">${lvl.stop.toFixed(2)}</div>
            <div className="text-[10px] text-zinc-500 mt-1">
              주당 위험 ${lvl.risk_per_share.toFixed(2)}
            </div>
          </div>
          <div className="rounded-lg bg-white dark:bg-zinc-900 p-3 border border-zinc-200 dark:border-zinc-800">
            <div className="text-xs text-zinc-500 mb-1">1차 목표 (1R)</div>
            <div className="font-mono font-bold text-emerald-600">${lvl.target_1r.toFixed(2)}</div>
            <div className="text-[10px] text-zinc-500 mt-1">
              +{((lvl.target_1r - lvl.entry) / lvl.entry * 100).toFixed(2)}%
            </div>
          </div>
          <div className="rounded-lg bg-white dark:bg-zinc-900 p-3 border border-zinc-200 dark:border-zinc-800">
            <div className="text-xs text-zinc-500 mb-1">2차 목표 (2R)</div>
            <div className="font-mono font-bold text-emerald-700">${lvl.target_2r.toFixed(2)}</div>
            <div className="text-[10px] text-zinc-500 mt-1">
              +{((lvl.target_2r - lvl.entry) / lvl.entry * 100).toFixed(2)}%
            </div>
          </div>
        </div>
      ) : (
        <div className="mb-4 text-xs text-zinc-500 italic">변동성 데이터 부족 — 진입 정보 계산 불가</div>
      )}

      {/* 사이즈 / 위험 */}
      {lvl && (
        <div className="flex items-center justify-between text-xs mb-4 px-3 py-2 rounded-lg bg-zinc-100 dark:bg-zinc-800">
          <div>
            <span className="text-zinc-500">주식 수</span>{" "}
            <span className="font-mono font-bold">{lvl.qty}</span>
          </div>
          <div>
            <span className="text-zinc-500">매입금액</span>{" "}
            <span className="font-mono">${lvl.position_value.toFixed(0)}</span>
          </div>
          <div>
            <span className="text-zinc-500">계좌 위험</span>{" "}
            <span className="font-mono font-bold text-rose-600">${lvl.account_risk_dollar.toFixed(0)}</span>
          </div>
        </div>
      )}

      {/* 점수 분해 (5차원) */}
      <div className="space-y-1 mb-4">
        <ScoreBar label="추세 정렬" value={c.score_breakdown.trend_alignment} max={2} color="bg-blue-500" />
        <ScoreBar label="모멘텀" value={c.score_breakdown.momentum} max={2} color="bg-violet-500" />
        <ScoreBar label="거래량" value={c.score_breakdown.volume_strength} max={1} color="bg-emerald-500" />
        <ScoreBar label="돌파" value={c.score_breakdown.breakout} max={1} color="bg-orange-500" />
        {c.score_breakdown.negatives > 0 && (
          <ScoreBar label="감점" value={c.score_breakdown.negatives} max={3} color="bg-rose-500" />
        )}
      </div>

      {/* 백테스트 통계 */}
      {hist && (
        <div className="mb-4 p-2 rounded-lg bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-900 text-xs">
          <div className="font-medium text-emerald-700 dark:text-emerald-300 mb-0.5">📊 과거 백테스트 검증</div>
          <div className="font-mono">
            <span className={hist.hit_rate >= 0.7 ? "font-bold text-emerald-600" : "text-zinc-700 dark:text-zinc-300"}>
              {(hist.hit_rate * 100).toFixed(0)}%
            </span>{" "}
            흑자 (n={hist.n}회), 평균{" "}
            <span className={hist.avg_ret > 0.02 ? "font-bold text-emerald-600" : "text-zinc-700 dark:text-zinc-300"}>
              {hist.avg_ret >= 0 ? "+" : ""}
              {(hist.avg_ret * 100).toFixed(2)}%
            </span>{" "}
            / 5일
          </div>
        </div>
      )}

      {/* 자연어 reasons */}
      <div className="mb-3">
        <div className="text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1.5">왜 이 종목인가</div>
        <ul className="space-y-1 text-xs">
          {c.reasons.map((r, idx) => (
            <li key={idx} className="flex items-start gap-2">
              <span className={POLARITY_CLS[r.polarity]}>{POLARITY_EMOJI[r.polarity]}</span>
              <div>
                <span className="font-medium">{r.label}</span>
                <span className="text-zinc-500"> — {r.detail}</span>
              </div>
            </li>
          ))}
        </ul>
      </div>

      {/* 차트 버튼 */}
      {onChartClick && (
        <button
          onClick={() => onChartClick(c.symbol)}
          className="w-full px-3 py-2 rounded-lg bg-zinc-900 hover:bg-zinc-800 dark:bg-zinc-100 dark:hover:bg-zinc-200 text-white dark:text-zinc-900 text-sm font-medium transition-colors flex items-center justify-center gap-2"
          aria-label={`${c.symbol} 차트 보기`}
        >
          📈 차트 + 진입선 보기
        </button>
      )}
    </div>
  );
}

function TierSection({ tier, candidates, onChartClick }: { tier: string; candidates: DashboardCandidate[]; onChartClick?: (symbol: string) => void }) {
  const meta = TIER_META[tier];
  if (candidates.length === 0) return null;
  return (
    <section className="mb-8">
      <div className="mb-3">
        <h2 className="text-lg font-bold">
          {meta.label} <span className="text-zinc-500 font-normal text-sm">({candidates.length})</span>
        </h2>
        <p className="text-xs text-zinc-600 dark:text-zinc-400">{meta.sub}</p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {candidates.map((c) => (
          <CandidateCard key={c.symbol} c={c} onChartClick={onChartClick} />
        ))}
      </div>
    </section>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  // 설정
  const [equity, setEquity] = useState(25000);
  const [riskPct, setRiskPct] = useState(0.5); // 0.5% per trade
  const [scoreMin, setScoreMin] = useState(2);
  const [earningsMode, setEarningsMode] = useState<"pre_only" | "exclude" | "off">("pre_only");
  const [atrMult, setAtrMult] = useState(2.0);
  const [tierStrictness, setTierStrictness] = useState(3); // 1~5, 3=표준

  // 차트 모달
  const [chartSymbol, setChartSymbol] = useState<string | null>(null);
  const chartCandidate = useMemo(() => {
    if (!chartSymbol || !data) return null;
    for (const tier of ["S", "A", "B", "C"] as const) {
      const found = data.tiers[tier].find((c) => c.symbol === chartSymbol);
      if (found) return found;
    }
    return null;
  }, [chartSymbol, data]);

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
      const d = await fetchDashboardToday({
        scoreMin,
        earningsMode,
        equity,
        riskPerTrade: riskPct / 100,
        atrMult,
        tierStrictness,
        top: 50,
      });
      setData(d);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [equity, riskPct, scoreMin, earningsMode, atrMult, tierStrictness]);

  const summary = useMemo(() => {
    if (!data) return null;
    return [
      { label: "🥇 S", value: data.n_tier_s },
      { label: "🥈 A", value: data.n_tier_a },
      { label: "🥉 B", value: data.n_tier_b },
      { label: "👀 C", value: data.n_tier_c },
    ];
  }, [data]);

  return (
    <main className="min-h-full px-6 py-8 max-w-6xl mx-auto">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mb-4">
        <TopNav />
      </div>
      <header className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-2xl font-bold">🎯 통합 대시보드</h1>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <button
              onClick={() => setHelpOpen(!helpOpen)}
              className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                helpOpen
                  ? "border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-400 dark:bg-blue-950 dark:text-blue-300"
                  : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
              }`}
              title="Tier·시그널·진입가 해석 (ESC로 닫기)"
            >
              ❓ 도움말
            </button>
          </div>
        </div>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          시그널 검증 (scan_momentum) + 운용 정보 (entry/stop/target/qty) + 자연어 설명
        </p>
      </header>

      {/* 시장 상태 + Tier 요약 */}
      {data && (
        <section className="mb-6 grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Regime */}
          <div
            className={`rounded-lg p-4 border-2 col-span-1 ${
              data.regime.on
                ? "bg-emerald-50 dark:bg-emerald-950/30 border-emerald-300 dark:border-emerald-800"
                : "bg-rose-50 dark:bg-rose-950/30 border-rose-300 dark:border-rose-800"
            }`}
          >
            <div className="text-xs text-zinc-500 mb-1">시장 상태</div>
            <div className="text-lg font-bold">{data.regime.on ? "🟢 진입 가능" : "🛑 진입 차단"}</div>
            <div className="text-xs text-zinc-600 dark:text-zinc-400 mt-1">
              {data.regime.spy_above_ma !== null && (
                <>S&P {data.regime.spy_above_ma ? "200일선 위" : "200일선 아래"}</>
              )}
              {data.regime.vix_close !== null && (
                <> · 변동성 {data.regime.vix_close.toFixed(1)}</>
              )}
            </div>
          </div>

          {/* Tier 요약 */}
          <div className="rounded-lg p-4 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 col-span-1">
            <div className="text-xs text-zinc-500 mb-2">Tier 분류</div>
            <div className="flex items-center justify-between gap-2 text-sm">
              {summary?.map((s) => (
                <div key={s.label} className="text-center">
                  <div className="font-bold text-lg">{s.value}</div>
                  <div className="text-[10px] text-zinc-500">{s.label}</div>
                </div>
              ))}
            </div>
          </div>

          {/* 기준일자 + 후보 수 */}
          <div className="rounded-lg p-4 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 col-span-1">
            <div className="text-xs text-zinc-500 mb-1">기준 일자</div>
            <div className="text-lg font-bold">{data.as_of}</div>
            <div className="text-xs text-zinc-500 mt-1">
              총 {data.n_candidates}개 후보 (점수 ≥ {data.config.score_min}, {
                data.config.earnings_mode === "pre_only" ? "PEAD 허용" :
                data.config.earnings_mode === "exclude" ? "실적 모두 차단" :
                "실적 무시"
              })
            </div>
          </div>
        </section>
      )}

      {/* 설정 패널 */}
      <section className="mb-6 p-4 rounded-lg bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm items-end">
          <div>
            <label className="block text-xs text-zinc-500 mb-1">계좌 자본 (USD)</label>
            <input
              type="number"
              value={equity}
              onChange={(e) => setEquity(Number(e.target.value))}
              className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5 font-mono"
              step={1000}
              min={1000}
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1">트레이드당 위험 (%)</label>
            <input
              type="number"
              value={riskPct}
              onChange={(e) => setRiskPct(Number(e.target.value))}
              className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5 font-mono"
              step={0.1}
              min={0.1}
              max={5}
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1">최소 점수</label>
            <select
              value={scoreMin}
              onChange={(e) => setScoreMin(Number(e.target.value))}
              className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5"
            >
              {[1, 2, 3, 4, 5].map((s) => (
                <option key={s} value={s}>
                  {s} 이상
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1">실적 처리</label>
            <select
              value={earningsMode}
              onChange={(e) => setEarningsMode(e.target.value as "pre_only" | "exclude" | "off")}
              className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1.5"
            >
              <option value="pre_only">실적 임박만 차단 (PEAD 허용, 권장)</option>
              <option value="exclude">실적 ±5일 모두 차단</option>
              <option value="off">차단 없음</option>
            </select>
          </div>
        </div>

        {/* ATR 배수 슬라이더 */}
        <div className="mt-4 pt-4 border-t border-zinc-200 dark:border-zinc-800">
          <div className="flex items-baseline justify-between mb-2">
            <label htmlFor="atr-mult-slider" className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              손절 거리 (ATR 배수)
            </label>
            <div className="text-sm font-mono">
              <span className={`font-bold ${
                atrMult <= 1.4 ? "text-rose-600" :
                atrMult <= 2.2 ? "text-emerald-600" :
                "text-amber-600"
              }`}>{atrMult.toFixed(1)}×</span>
              <span className="text-zinc-500 ml-2 text-xs">
                {atrMult <= 1.4 ? "타이트 (휩쏘 위험↑, 주식수↑)" :
                 atrMult <= 2.2 ? "표준 (균형)" :
                 atrMult <= 2.8 ? "넉넉함 (장기보유, 주식수↓)" :
                 "매우 넉넉 (큰 변동 허용)"}
              </span>
            </div>
          </div>
          <input
            id="atr-mult-slider"
            type="range"
            min={1.0}
            max={3.5}
            step={0.1}
            value={atrMult}
            onChange={(e) => setAtrMult(Number(e.target.value))}
            className="w-full accent-violet-600 cursor-pointer"
          />
          <div className="flex justify-between text-[10px] text-zinc-500 font-mono mt-1 px-1">
            <span>1.0× (타이트)</span>
            <span>1.5×</span>
            <span>2.0× (기본)</span>
            <span>2.5×</span>
            <span>3.0×</span>
            <span>3.5× (넉넉)</span>
          </div>
        </div>

        <div className="text-xs text-zinc-500 mt-3">
          ATR(14) × <span className="font-mono font-semibold">{atrMult.toFixed(1)}</span>배 거리로 손절 자동 설정. 1R = 손절거리만큼 위, 2R = 두 배 위. 주식 수는 계좌×위험% / 주당위험.
          {atrMult <= 1.4 && (
            <div className="mt-1 text-rose-600">
              ⚠️ 타이트한 손절은 일반적인 변동성에도 휩쏘(가짜 손절) 발생 위험이 큽니다.
            </div>
          )}
          {atrMult >= 2.8 && (
            <div className="mt-1 text-amber-600">
              ⚠️ 넉넉한 손절은 주당 위험이 커서 같은 계좌 위험으로 살 수 있는 주식 수가 줄어듭니다.
            </div>
          )}
        </div>

        {/* Tier 엄격도 슬라이더 */}
        <div className="mt-4 pt-4 border-t border-zinc-200 dark:border-zinc-800">
          <div className="flex items-baseline justify-between mb-2">
            <label htmlFor="tier-strict-slider" className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Tier 분류 엄격도
            </label>
            <div className="text-sm font-mono">
              <span className={`font-bold ${
                tierStrictness <= 2 ? "text-rose-600" :
                tierStrictness === 3 ? "text-emerald-600" :
                "text-blue-600"
              }`}>
                Lv.{tierStrictness}
              </span>
              <span className="text-zinc-500 ml-2 text-xs">
                {tierStrictness === 1 ? "매우 엄격 — Tier S 거의 없음" :
                 tierStrictness === 2 ? "엄격 — 높은 신뢰도 필수" :
                 tierStrictness === 3 ? "표준 — 균형 (권장)" :
                 tierStrictness === 4 ? "완화 — 더 많은 후보" :
                 "매우 완화 — 빈도 최우선"}
              </span>
            </div>
          </div>
          <input
            id="tier-strict-slider"
            type="range"
            min={1}
            max={5}
            step={1}
            value={tierStrictness}
            onChange={(e) => setTierStrictness(Number(e.target.value))}
            className="w-full accent-violet-600 cursor-pointer"
          />
          <div className="flex justify-between text-[10px] text-zinc-500 font-mono mt-1 px-1">
            <span>1 (매우 엄격)</span>
            <span>2 (엄격)</span>
            <span>3 (표준)</span>
            <span>4 (완화)</span>
            <span>5 (매우 완화)</span>
          </div>
          {/* 현재 임계값 표시 */}
          {data?.config?.tier_thresholds && (
            <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
              <div className="rounded-md bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-900 p-2">
                <div className="font-semibold text-emerald-700 dark:text-emerald-300 mb-0.5">🥇 Tier S (통계강도)</div>
                <div className="text-zinc-600 dark:text-zinc-400 font-mono">
                  hit ≥ {((data.config.tier_thresholds as Record<string, number>).s_stats_hit * 100).toFixed(0)}%,
                  avg ≥ {((data.config.tier_thresholds as Record<string, number>).s_stats_avg * 100).toFixed(1)}%,
                  표본 ≥ {(data.config.tier_thresholds as Record<string, number>).s_stats_n}
                </div>
              </div>
              <div className="rounded-md bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900 p-2">
                <div className="font-semibold text-blue-700 dark:text-blue-300 mb-0.5">🥈 Tier A</div>
                <div className="text-zinc-600 dark:text-zinc-400 font-mono">
                  hit ≥ {((data.config.tier_thresholds as Record<string, number>).a_hit * 100).toFixed(0)}%,
                  표본 ≥ {(data.config.tier_thresholds as Record<string, number>).a_n}
                </div>
              </div>
              <div className="rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 p-2">
                <div className="font-semibold text-amber-700 dark:text-amber-300 mb-0.5">🥉 Tier B</div>
                <div className="text-zinc-600 dark:text-zinc-400 font-mono">
                  hit ≥ {((data.config.tier_thresholds as Record<string, number>).b_hit * 100).toFixed(0)}%,
                  표본 ≥ {(data.config.tier_thresholds as Record<string, number>).b_n}
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      {err && (
        <div className="mb-4 p-4 rounded-lg bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-300">
          오류: {err}
          <div className="text-xs mt-1 text-zinc-600 dark:text-zinc-400">
            서버가 새 endpoint를 인식하지 못했을 수 있습니다. API 서버를 재시작 후 다시 시도하세요.
          </div>
        </div>
      )}

      {loading && !data && (
        <div className="p-8 text-center text-zinc-500">데이터 불러오는 중...</div>
      )}

      {/* Tier별 카드 */}
      {data && (
        <>
          <TierSection tier="S" candidates={data.tiers.S} onChartClick={setChartSymbol} />
          <TierSection tier="A" candidates={data.tiers.A} onChartClick={setChartSymbol} />
          <TierSection tier="B" candidates={data.tiers.B} onChartClick={setChartSymbol} />
          {data.tiers.C.length > 0 && (
            <details className="mb-6">
              <summary className="cursor-pointer text-sm text-zinc-600 dark:text-zinc-400 hover:underline">
                👀 Tier C ({data.tiers.C.length}건) — 펼쳐 보기
              </summary>
              <div className="mt-3">
                <TierSection tier="C" candidates={data.tiers.C} onChartClick={setChartSymbol} />
              </div>
            </details>
          )}
          {data.n_candidates === 0 && (
            <div className="p-8 text-center text-zinc-500 rounded-lg border border-zinc-200 dark:border-zinc-800">
              조건에 맞는 후보 없음. 필터를 완화하거나 다른 날짜로 검색해 보세요.
            </div>
          )}
        </>
      )}

      {/* Path 범례 (collapsible) */}
      <details className="mt-8 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/50">
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium hover:bg-zinc-100 dark:hover:bg-zinc-800/50 rounded-lg">
          🏷️ 통과 경로 범례 — 각 종목이 어떤 룰로 Tier에 올랐는지
        </summary>
        <div className="px-4 pb-4 pt-2 text-xs space-y-2">
          <p className="text-zinc-600 dark:text-zinc-400">
            모든 종목은 Tier(S/A/B/C)에 더해 <strong>통과 경로(path)</strong>를 가집니다. Tier가 같아도 경로가 다르면 진입 근거가 다릅니다.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-1 mt-2">
            {Object.entries(PATH_META).map(([key, meta]) => (
              <div key={key} className="flex items-start gap-2 py-1">
                <span className={`shrink-0 px-1.5 py-0.5 rounded text-[11px] font-medium ${meta.cls}`}>
                  {meta.label}
                </span>
                <span className="text-zinc-600 dark:text-zinc-400 leading-snug">{meta.tip}</span>
              </div>
            ))}
          </div>
          <p className="pt-2 mt-2 border-t border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-400">
            💡 <strong>Tier S</strong>는 셋 중 하나만 통과해도 인정됩니다 — 시그널 완벽 정렬(perfect) / 통계 강도(stats) / 표본 압도(battle).
          </p>
        </div>
      </details>

      {/* 차트 모달 */}
      {chartSymbol && (
        <ChartModal
          symbol={chartSymbol}
          onClose={() => setChartSymbol(null)}
          entry={chartCandidate?.levels?.entry}
          stop={chartCandidate?.levels?.stop}
          target1r={chartCandidate?.levels?.target_1r}
          target2r={chartCandidate?.levels?.target_2r}
          atrMult={atrMult}
          equity={equity}
          riskPerTrade={riskPct / 100}
        />
      )}
    </main>
  );
}
