"use client";

import { useEffect, useState } from "react";

import TopNav from "@/components/TopNav";
import {
  fetchPicksToday,
  triggerPicksRefresh,
  type DailyPick,
} from "@/lib/api";
import HelpDrawer from "./HelpDrawer";
import MorningBriefCard from "./MorningBriefCard";

const STRATEGY_LABEL: Record<string, string> = {
  swing: "스윙",
  day: "단타",
};

const MARKET_INDEX_LABEL: Record<string, string> = {
  spy: "S&P 500 (SPY)",
  qqq: "나스닥 100 (QQQ)",
  iwm: "러셀 2000 (IWM)",
};

function gradeFromScore(score: number): { label: string; color: string } {
  // v3 기준 (100점 만점)
  if (score >= 75) return { label: "A", color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" };
  if (score >= 60) return { label: "B", color: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300" };
  if (score >= 40) return { label: "C", color: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300" };
  return { label: "D", color: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300" };
}

const CATALYST_LABEL: Record<string, string> = {
  earnings: "실적 발표 임박",
  fda_ma: "FDA / 인수합병",
  upgrade: "애널리스트 상향",
  news: "관련 뉴스",
  none: "재료 없음",
};

type Reason = {
  label: string;
  detail: string;
  positive: boolean;
};

function buildReasons(pick: DailyPick): Reason[] {
  const r = pick.score_breakdown.rationale ?? {};
  const sb = pick.score_breakdown;
  const reasons: Reason[] = [];

  // 1. 갭
  if (typeof r.gap_pct === "number" && r.gap_pct !== 0) {
    const sign = r.gap_pct > 0 ? "상승" : "하락";
    const mag = Math.abs(r.gap_pct);
    let strength = "";
    if (mag >= 8) strength = "강한 ";
    else if (mag >= 4) strength = "뚜렷한 ";
    else if (mag >= 2) strength = "";
    if (mag >= 2) {
      reasons.push({
        label: `갭 ${sign}`,
        detail: `${strength}${mag.toFixed(2)}% 갭${sign} (전일 종가 대비)`,
        positive: r.gap_pct > 0,
      });
    }
  }

  // 2. RVOL
  if (typeof r.rvol === "number" && r.rvol >= 1.5) {
    reasons.push({
      label: "거래량 폭증",
      detail: `상대거래량 ${r.rvol.toFixed(1)}× — 평소보다 ${r.rvol.toFixed(1)}배 거래`,
      positive: true,
    });
  }

  // 3. 카탈리스트
  if (r.catalyst_kind && r.catalyst_kind !== "none") {
    reasons.push({
      label: "재료 보유",
      detail: `${CATALYST_LABEL[r.catalyst_kind] ?? r.catalyst_kind}${
        pick.catalyst_summary ? ` — ${pick.catalyst_summary}` : ""
      }`,
      positive: true,
    });
  }

  // 4. 차트 셋업
  const setupParts: string[] = [];
  if (r.tight_flag) setupParts.push("좁은 박스 돌파 직전");
  if (r.breakout_20d) setupParts.push("20일 신고가 돌파");
  if (r.near_52w_high) setupParts.push("52주 고점 근접");
  if (setupParts.length > 0) {
    reasons.push({
      label: "차트 셋업",
      detail: setupParts.join(" + "),
      positive: true,
    });
  }

  // 5. 섹터 동조
  if (r.sector_aligned === true && r.sector_etf) {
    const gap = r.sector_etf_gap;
    reasons.push({
      label: "섹터 동조",
      detail: `${r.sector_etf} (섹터 ETF) ${
        gap !== null && gap !== undefined ? `${gap >= 0 ? "+" : ""}${gap.toFixed(2)}%` : ""
      } — 같은 방향으로 움직임`,
      positive: true,
    });
  } else if (r.sector_aligned === false) {
    reasons.push({
      label: "섹터 역행",
      detail: `섹터 ETF는 반대 방향 — 종목 단독 모멘텀`,
      positive: false,
    });
  }

  // 6. WHITELIST
  if (r.is_whitelist || (sb.s5_whitelist ?? 0) > 0) {
    reasons.push({
      label: "검증 종목",
      detail: "과거 백테스트에서 알파가 입증된 WHITELIST 멤버",
      positive: true,
    });
  }

  // v3 신규 reasons
  // Stage 2 Trend Template
  if (r.stage2_pass) {
    reasons.push({
      label: "Stage 2 추세",
      detail: "MA 정렬·52w 고점 근접·RS≥70 — 강한 상승 추세 단계",
      positive: true,
    });
  }
  // Compression / Expansion
  if (r.compression && r.expansion) {
    reasons.push({
      label: "압축 후 폭발",
      detail: "변동성 축소 후 expansion 시작 — Minervini VCP 골든 시점",
      positive: true,
    });
  } else if (r.compression) {
    reasons.push({
      label: "변동성 압축",
      detail: "직전 5일 변동성이 30일 평균의 70% 이하 — 폭발 직전 가능성",
      positive: true,
    });
  } else if (r.expansion) {
    reasons.push({
      label: "변동성 확장",
      detail: "오늘 ATR이 5일 평균의 150% 이상 — 추세 폭발 진행중",
      positive: true,
    });
  }
  // Open Location
  if (r.open_location_above_pivot) {
    reasons.push({
      label: "피벗 위 시초",
      detail: "시초가가 피벗 가격 위 — 매수 우위 진입 위치",
      positive: true,
    });
  } else if (r.open_location_above_prev_high) {
    reasons.push({
      label: "전일 고점 돌파",
      detail: "시초가가 전일 고점 위 — 강세 continuation",
      positive: true,
    });
  }
  // RSI Structure
  if (r.rsi_structure_grade === "good") {
    reasons.push({
      label: "RSI 강세 + higher low",
      detail: r.rsi_structure_notes ?? "RSI 50-75 영역에서 모멘텀 유지",
      positive: true,
    });
  } else if (r.rsi_structure_grade === "bad") {
    reasons.push({
      label: "RSI 위험",
      detail: r.rsi_structure_notes ?? "다이버전스 또는 climax 위험",
      positive: false,
    });
  }

  return reasons;
}

function RegimeBanner({ pick }: { pick: DailyPick | undefined }) {
  if (!pick || !pick.score_breakdown) return null;
  const sb = pick.score_breakdown;
  const score = sb.block_0 ?? sb.b0_regime ?? 0;
  let mode = "중립";
  let modeColor = "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300";
  let desc = "";
  if (score >= 12) {
    mode = "공격";
    modeColor = "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300";
    desc = "강세장 — 적극 진입 환경";
  } else if (score >= 7) {
    mode = "중립";
    modeColor = "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300";
    desc = "혼조 — 포지션 사이즈 축소(×0.7)";
  } else {
    mode = "방어";
    modeColor = "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300";
    desc = "약세장 — long 진입 차단, 평균회귀만";
  }
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          시장 Regime
        </span>
        <span className={`rounded px-2 py-0.5 text-sm font-bold ${modeColor}`}>
          {mode} 모드
        </span>
        <span className="text-sm font-mono text-zinc-700 dark:text-zinc-300">
          {score.toFixed(1)}/15
        </span>
        <span className="text-xs text-zinc-500">— {desc}</span>
      </div>
    </section>
  );
}

export default function PicksPage() {
  const [picks, setPicks] = useState<DailyPick[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  function load() {
    fetchPicksToday()
      .then(setPicks)
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    load();
  }, []);

  // ESC로 도움말 닫기
  useEffect(() => {
    if (!helpOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHelpOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [helpOpen]);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      await triggerPicksRefresh();
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setRefreshing(false);
    }
  }

  const top = picks?.filter((p) => !p.is_backup) ?? [];
  const backup = picks?.filter((p) => p.is_backup) ?? [];
  const market = top[0]?.market_context ?? {};

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-6xl space-y-6">
        <TopNav />
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              오늘의 종목
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              매일 08:55 ET 자동 선정, 09:15 ET 이메일 발송 — Hard Gate 12개 + 100점 만점 5-Block 점수
            </p>
          </div>
          <nav className="flex flex-wrap gap-2">
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="rounded-lg border border-blue-300 bg-blue-50 px-3 py-2 text-sm text-blue-700 hover:bg-blue-100 disabled:opacity-50 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300"
            >
              {refreshing ? "종목 다시 선정 중 (예상 30초)" : "↻ 종목 다시 선정"}
            </button>
            <button
              onClick={() => setHelpOpen(!helpOpen)}
              className={`rounded-lg border px-3 py-2 text-sm transition-colors ${
                helpOpen
                  ? "border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-400 dark:bg-blue-950 dark:text-blue-300"
                  : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
              }`}
              title="카드 항목·점수표·등급 해석 (ESC로 닫기)"
            >
              ❓ 도움말
            </button>
          </nav>
        </header>

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            ✗ 종목 정보를 불러오지 못했습니다 — {error}
          </div>
        )}

        <MorningBriefCard />

        <RegimeBanner pick={top[0] ?? backup[0]} />

        <MarketContextCard ctx={market} />

        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-black dark:text-zinc-50">
            진입 후보 Top 3 ({top.length})
          </h2>
          {picks === null ? (
            <p className="text-zinc-500">오늘 종목 불러오는 중…</p>
          ) : top.length === 0 ? (
            <EmptyHint />
          ) : (
            <div className="grid gap-3 lg:grid-cols-3">
              {top.map((p) => (
                <PickCard key={p.id} pick={p} />
              ))}
            </div>
          )}
        </section>

        {backup.length > 0 && (
          <section className="space-y-3">
            <h2 className="text-xl font-semibold text-zinc-700 dark:text-zinc-300">
              백업 — 섹터 중복/대체 후보 ({backup.length})
            </h2>
            <div className="grid gap-3 lg:grid-cols-2">
              {backup.map((p) => (
                <PickCard key={p.id} pick={p} muted />
              ))}
            </div>
          </section>
        )}

        <footer className="pt-4 text-xs text-zinc-500">
          현재 모의투자(paper) 단계입니다. 실거래 전환은 별도 검증 후 진행됩니다.
        </footer>
      </div>
    </main>
  );
}

function EmptyHint() {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-6 text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
      <p>오늘의 종목이 아직 선정되지 않았습니다.</p>
      <p className="mt-2">
        우상단의 <span className="font-medium text-blue-700 dark:text-blue-300">↻ 종목 다시 선정</span> 버튼을 눌러 지금 선정하거나, 선정 가능 시간(매일 오전 8:55 ET)을 기다려 주세요.
      </p>
      <details className="mt-4 text-xs text-zinc-500">
        <summary className="cursor-pointer">관리자용 (CLI 명령어)</summary>
        <div className="mt-2 space-y-1">
          <code className="block rounded bg-zinc-100 p-2 dark:bg-zinc-800">
            python -m scanner.stage2_daily_picks
          </code>
          <p>유니버스가 비어있을 경우 먼저:</p>
          <code className="block rounded bg-zinc-100 p-2 dark:bg-zinc-800">
            python -m scanner.stage1_universe --refresh
          </code>
        </div>
      </details>
    </div>
  );
}

function MarketContextCard({ ctx }: { ctx: Record<string, number> }) {
  const entries = Object.entries(ctx).filter(([k]) => k.endsWith("_gap_pct"));
  if (entries.length === 0) return null;
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-500">
        시장 분위기 (주요 지수 갭)
      </h2>
      <div className="flex flex-wrap gap-3 text-sm">
        {entries.map(([k, v]) => {
          const ticker = k.replace("_gap_pct", "");
          const label = MARKET_INDEX_LABEL[ticker] ?? ticker.toUpperCase();
          return (
            <div
              key={k}
              className="rounded bg-zinc-100 px-3 py-1 font-mono dark:bg-zinc-800"
              title={`${label} — 전일 종가 대비 개장 갭`}
            >
              <span className="text-zinc-500">{label}</span>{" "}
              <span className={v >= 0 ? "text-emerald-600" : "text-red-600"}>
                {v >= 0 ? "+" : ""}
                {v.toFixed(2)}%
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function PickCard({ pick, muted = false }: { pick: DailyPick; muted?: boolean }) {
  const score = parseFloat(pick.total_score);
  const pivot = parseFloat(pick.pivot_price);
  const stop = parseFloat(pick.stop_price);
  const t1 = parseFloat(pick.target_1r);
  const t2 = parseFloat(pick.target_2r);
  const risk = parseFloat(pick.risk_per_share);
  const riskPct = (risk / pivot) * 100;
  const grade = gradeFromScore(score);
  const strategyLabel = STRATEGY_LABEL[pick.strategy_tag] ?? pick.strategy_tag;

  return (
    <div
      className={`rounded-lg border bg-white p-4 dark:bg-zinc-900 ${
        muted
          ? "border-zinc-200 opacity-80 dark:border-zinc-800"
          : "border-blue-300 dark:border-blue-900"
      }`}
    >
      <div className="flex items-baseline justify-between">
        <div>
          <span className="text-xs text-zinc-500">#{pick.rank}</span>{" "}
          <span className="text-2xl font-bold text-black dark:text-zinc-50">
            {pick.symbol}
          </span>
          <span
            className={`ml-2 rounded px-1.5 py-0.5 text-xs font-mono ${
              pick.strategy_tag === "swing"
                ? "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300"
                : "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
            }`}
            title={pick.strategy_tag === "swing" ? "스윙 — 며칠~몇주 보유" : "단타 — 당일 청산"}
          >
            {strategyLabel}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`rounded px-1.5 py-0.5 text-xs font-bold ${grade.color}`}
            title={`등급 ${grade.label} — 18점↑=A, 14점↑=B, 그 외 C`}
          >
            {grade.label}
          </span>
          <span
            className="text-lg font-mono text-blue-600 dark:text-blue-400"
            title="총 점수 (24점 만점)"
          >
            {score.toFixed(1)}
          </span>
        </div>
      </div>
      {pick.sector && (
        <p className="text-xs text-zinc-500">{pick.sector}</p>
      )}

      <ReasonList reasons={buildReasons(pick)} />

      <IndicatorBar rationale={pick.score_breakdown.rationale} />

      <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <Row label="진입가" tooltip="Pivot — 매수 진입 기준 가격" value={`$${pivot.toFixed(2)}`} />
        <Row label="손절가" tooltip="Stop — 손실 제한 매도 기준" value={`$${stop.toFixed(2)}`} negative />
        <Row label="1차 목표" tooltip="1R — 위험 1배만큼의 수익 가격" value={`$${t1.toFixed(2)}`} positive />
        <Row label="2차 목표" tooltip="2R — 위험 2배만큼의 수익 가격" value={`$${t2.toFixed(2)}`} positive />
        <Row label="주식수" tooltip="Position Size — 자본 대비 위험 비율로 계산" value={`${pick.position_size}주`} />
        <Row label="위험비율" tooltip="(진입가 − 손절가) / 진입가" value={`${riskPct.toFixed(2)}%`} negative />
      </dl>

      <BlockBreakdown sb={pick.score_breakdown} />
    </div>
  );
}

function BlockBreakdown({ sb }: { sb: import("@/lib/api").ScoreBreakdown }) {
  // v3 5-block. 누락(=undefined) 항목 0으로
  const b0 = sb.block_0 ?? sb.b0_regime ?? 0;
  const ba = sb.block_a ?? 0;
  const bb = sb.block_b ?? 0;
  const bc = sb.block_c ?? 0;
  const bd = sb.block_d ?? 0;
  const pen = sb.penalties_total ?? 0;
  const blocks = [
    { name: "시장 체력", short: "0", value: b0, max: 15, color: "bg-zinc-500", tooltip: "Block 0 — Market Regime: SPY/QQQ/IWM 추세 + VIX + breadth" },
    { name: "추세·강도", short: "A", value: ba, max: 25, color: "bg-blue-500", tooltip: "Block A — Trend & RS: RS Rating + 1m/3m/6m 모멘텀 + Stage 2 추세" },
    { name: "재료·거래량", short: "B", value: bb, max: 20, color: "bg-amber-500", tooltip: "Block B — Catalyst & Volume: RVOL + 거래량 surge + 카탈리스트 종류" },
    { name: "셋업 품질", short: "C", value: bc, max: 25, color: "bg-emerald-500", tooltip: "Block C — Setup: 패턴 + 피벗 근접 + 베이스 + Open Location + Compression/Expansion" },
    { name: "리스크", short: "D", value: bd, max: 15, color: "bg-orange-500", tooltip: "Block D — Risk: RSI 구조 + 베타 + ATR-RR + 섹터 강도" },
  ];
  return (
    <div className="mt-3 space-y-1.5">
      {blocks.map((b) => (
        <div key={b.short} className="flex items-center gap-2 text-xs" title={b.tooltip}>
          <span className="w-16 shrink-0 text-zinc-600 dark:text-zinc-400">{b.name}</span>
          <div className="relative h-3 flex-1 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800">
            <div
              className={`h-full ${b.color}`}
              style={{ width: `${Math.min(100, (b.value / b.max) * 100)}%` }}
            />
          </div>
          <span className="w-14 shrink-0 text-right font-mono text-zinc-700 dark:text-zinc-300">
            {b.value.toFixed(1)}/{b.max}
          </span>
        </div>
      ))}
      {pen > 0 && (
        <div className="flex items-center gap-2 text-xs" title="페널티 — RSI 구조 위반·climax·extended 등">
          <span className="w-16 shrink-0 text-red-600 dark:text-red-400">감점</span>
          <div className="relative h-3 flex-1 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800">
            <div
              className="h-full bg-red-500"
              style={{ width: `${Math.min(100, (pen / 15) * 100)}%` }}
            />
          </div>
          <span className="w-14 shrink-0 text-right font-mono text-red-600 dark:text-red-400">
            -{pen.toFixed(1)}/15
          </span>
        </div>
      )}
    </div>
  );
}

function IndicatorBar({ rationale }: { rationale?: import("@/lib/api").Rationale }) {
  if (!rationale) return null;
  const rsi = rationale.rsi_14;
  const volRatio = rationale.volume_vs_avg;

  // RSI 해석
  let rsiLabel = "—";
  let rsiColor = "text-zinc-500";
  let rsiTooltip = "RSI(14) — 14일 상대강도지수. 30↓=과매도, 70↑=과매수";
  if (typeof rsi === "number") {
    rsiLabel = rsi.toFixed(1);
    if (rsi >= 70) {
      rsiColor = "text-red-600 dark:text-red-400";
      rsiTooltip = `RSI ${rsi.toFixed(1)} — 과매수 영역 (≥70). 단기 과열, 추격 주의`;
    } else if (rsi >= 55) {
      rsiColor = "text-emerald-600 dark:text-emerald-400";
      rsiTooltip = `RSI ${rsi.toFixed(1)} — 강한 상승 모멘텀 (55~70)`;
    } else if (rsi <= 30) {
      rsiColor = "text-blue-600 dark:text-blue-400";
      rsiTooltip = `RSI ${rsi.toFixed(1)} — 과매도 영역 (≤30). 반등 후보`;
    } else if (rsi <= 45) {
      rsiColor = "text-amber-600 dark:text-amber-400";
      rsiTooltip = `RSI ${rsi.toFixed(1)} — 약세 (30~45)`;
    } else {
      rsiTooltip = `RSI ${rsi.toFixed(1)} — 중립 (45~55)`;
    }
  }

  // 거래량 비율 해석
  let volLabel = "—";
  let volColor = "text-zinc-500";
  let volTooltip = "전일 거래량 / 직전 20일 평균 거래량";
  if (typeof volRatio === "number") {
    volLabel = `${volRatio.toFixed(2)}×`;
    if (volRatio >= 2.0) {
      volColor = "text-emerald-600 dark:text-emerald-400";
      volTooltip = `전일 거래량 ${volRatio.toFixed(1)}× — 평소의 2배 이상 거래 (강한 관심)`;
    } else if (volRatio >= 1.3) {
      volColor = "text-blue-600 dark:text-blue-400";
      volTooltip = `전일 거래량 ${volRatio.toFixed(1)}× — 평소보다 활발`;
    } else if (volRatio < 0.7) {
      volColor = "text-amber-600 dark:text-amber-400";
      volTooltip = `전일 거래량 ${volRatio.toFixed(1)}× — 평소보다 적음 (관심 약함)`;
    }
  }

  return (
    <div className="mt-3 flex gap-2 text-xs">
      <div
        className="flex-1 rounded border border-zinc-200 bg-white px-2 py-1.5 dark:border-zinc-800 dark:bg-zinc-900"
        title={rsiTooltip}
      >
        <div className="text-[10px] uppercase tracking-wide text-zinc-500">RSI(14)</div>
        <div className={`font-mono text-sm font-semibold ${rsiColor}`}>{rsiLabel}</div>
      </div>
      <div
        className="flex-1 rounded border border-zinc-200 bg-white px-2 py-1.5 dark:border-zinc-800 dark:bg-zinc-900"
        title={volTooltip}
      >
        <div className="text-[10px] uppercase tracking-wide text-zinc-500">거래량 비율</div>
        <div className={`font-mono text-sm font-semibold ${volColor}`}>{volLabel}</div>
      </div>
    </div>
  );
}

function ReasonList({ reasons }: { reasons: Reason[] }) {
  if (reasons.length === 0) {
    return (
      <p className="mt-3 rounded bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-950 dark:text-zinc-400">
        뚜렷한 선정 이유가 잡히지 않았습니다 (점수 컷은 통과). 데이터 확인 권장.
      </p>
    );
  }
  return (
    <div className="mt-3 rounded border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-950">
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        왜 이 종목인가?
      </p>
      <ul className="space-y-1 text-xs">
        {reasons.map((r, i) => (
          <li key={i} className="flex items-start gap-1.5">
            <span
              className={`mt-0.5 ${
                r.positive
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-amber-600 dark:text-amber-400"
              }`}
            >
              {r.positive ? "▸" : "⚠"}
            </span>
            <span className="text-zinc-700 dark:text-zinc-300">
              <span className="font-medium">{r.label}</span>
              <span className="ml-1 text-zinc-600 dark:text-zinc-400">— {r.detail}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Row({
  label,
  tooltip,
  value,
  positive,
  negative,
}: {
  label: string;
  tooltip?: string;
  value: string;
  positive?: boolean;
  negative?: boolean;
}) {
  const color = positive
    ? "text-emerald-600 dark:text-emerald-400"
    : negative
    ? "text-red-600 dark:text-red-400"
    : "text-black dark:text-zinc-50";
  return (
    <>
      <dt className="text-zinc-500" title={tooltip}>
        {label}
      </dt>
      <dd className={`text-right font-mono ${color}`}>{value}</dd>
    </>
  );
}
