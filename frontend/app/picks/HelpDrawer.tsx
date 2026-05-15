"use client";

/**
 * Picks 페이지용 우측 슬라이드 도움말 드로어.
 * 데이터를 보면서 도움말을 동시에 읽을 수 있도록 비차단형 (옅은 backdrop만).
 */

import React from "react";

export default function HelpDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-black/5 transition-opacity ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        className={`fixed right-0 top-0 z-50 h-screen w-full max-w-md transform border-l border-zinc-200 bg-white shadow-2xl transition-transform duration-300 dark:border-zinc-800 dark:bg-zinc-950 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        role="dialog"
        aria-modal="false"
        aria-label="종목 카드 도움말"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
          <div>
            <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-50">
              📖 종목 카드 도움말
            </h2>
            <p className="text-[11px] text-zinc-500">v3 — 시장 Regime + 5-Block (100점)</p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
            aria-label="도움말 닫기"
            title="닫기 (ESC)"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 8.586l4.95-4.95 1.414 1.414L11.414 10l4.95 4.95-1.414 1.414L10 11.414l-4.95 4.95-1.414-1.414L8.586 10 3.636 5.05 5.05 3.636 10 8.586z" />
            </svg>
          </button>
        </div>

        <div className="h-[calc(100vh-4.5rem)] overflow-y-auto p-5 text-sm text-zinc-700 dark:text-zinc-300">
          {/* 1. 카드 구조 */}
          <Section title="카드 한눈에 보기" emoji="🎴">
            <p>각 종목 카드는 네 영역으로 구성:</p>
            <Bullets>
              <li><strong>헤더</strong> — 순위, 종목 코드, 기간 태그(스윙/단타), 등급(A/B/C/D), 총점</li>
              <li><strong>왜 이 종목인가?</strong> — 자동 생성된 선정 이유 (▸ 긍정 / ⚠ 주의)</li>
              <li><strong>지표 박스</strong> — RSI(14) + 거래량 비율</li>
              <li><strong>가격표 + 5-Block 점수표</strong> — 진입/손절/목표 + 점수 분석</li>
            </Bullets>
            <Note>
              예: <strong>#1 LRCX (스윙) C 53.0</strong> — 1순위, 스윙(2~5일), 등급 C, 총점 53/100
            </Note>
          </Section>

          {/* 2. 등급 */}
          <Section title="등급 (A/B/C/D)" emoji="🏅">
            <table className="w-full text-xs">
              <tbody>
                <Grade label="A" color="emerald" score="75 ↑" desc="강세 + 페널티 없음. 적극 진입" />
                <Grade label="B" color="blue" score="60 ~ 74" desc="정상 컷 통과. Top pick 가능성" />
                <Grade label="C" color="zinc" score="40 ~ 59" desc="lenient 모드 통과. 일부 블록 부족 가능" />
                <Grade label="D" color="amber" score="40 미만" desc="컷 미달 (DB 미저장)" />
              </tbody>
            </table>
          </Section>

          {/* 3. 선정 이유 라벨 */}
          <Section title="왜 이 종목인가? — 이유 라벨" emoji="🧩">
            <p>
              <span className="text-emerald-600">▸</span> 초록 = 긍정 / <span className="text-amber-600">⚠</span> 주황 = 주의
            </p>
            <Reason label="갭 상승 / 갭 하락" detail="±2% 이상부터 표시. 5%↑ 강한 갭, 8%↑ 매우 강함" />
            <Reason label="거래량 폭증" detail="RVOL ≥ 1.5×. 평소보다 N배 거래" />
            <Reason label="재료 보유" detail="실적, FDA, M&A, 업그레이드, 일반뉴스 카탈리스트 발견" />
            <Reason label="차트 셋업" detail="tight_flag, 20일 신고가 돌파, 52주 고점 근접 등" />
            <Reason label="섹터 동조" detail="종목 갭 방향과 섹터 ETF 갭 방향 일치 — 강세 흐름 근거" />
            <Reason label="섹터 역행" detail="섹터 ETF 반대 방향 — 종목 단독 모멘텀, 신뢰도 약함" warning />
            <Reason label="검증 종목" detail="WHITELIST 멤버 — 백테스트 알파 입증" />
            <Reason label="Stage 2 추세" detail="Minervini 8조건: MA 정렬·52w 고점 -25% 이내·RS≥70 등" />
            <Reason label="압축 후 폭발" detail="5d ATR ≤ 30d 평균의 70% (compression) AND 오늘 ATR ≥ 5d 평균의 150% (expansion) — VCP 골든 시점" />
            <Reason label="변동성 압축" detail="compression만 — 폭발 직전 가능성, 진입 타이밍 대기" />
            <Reason label="변동성 확장" detail="expansion만 — 추세 폭발 진행 중, 추격 주의" />
            <Reason label="피벗 위 시초" detail="시초가가 피벗(전일 고점·PMH) 위에서 시작 — 매수 우위" />
            <Reason label="전일 고점 돌파" detail="시초가가 전일 고점 위 — 강세 continuation" />
            <Reason label="RSI 강세 + higher low" detail="RSI 50~75 영역에서 직전 5봉 higher low 패턴" />
            <Reason label="RSI 위험" detail="베어리시 다이버전스 또는 RSI 85+ AND 거래량 climax" warning />
          </Section>

          {/* 4. RSI 해석 */}
          <Section title="RSI(14) 해석 (구조 기반)" emoji="📊">
            <p>v3는 절대값 페널티가 아닌 <strong>구조 기반</strong>으로 평가.</p>
            <table className="w-full text-xs">
              <tbody>
                <Grade label="< 30" color="blue" score="과매도" desc="반등 후보 (갭상승 결합 시 강함)" />
                <Grade label="30 ~ 50" color="amber" score="약세/중립" desc="모멘텀 부족" />
                <Grade label="50~75 + higher low" color="emerald" score="강세 (GOOD)" desc="추세 중. 진입 우호" />
                <Grade label="75~90 + higher highs" color="zinc" score="super leader" desc="NVDA·SMCI 같은 강세 leader. 페널티 없음" />
                <Grade label="85+ + climax" color="rose" score="과열 (BAD)" desc="단기 정점. P5 페널티 -3" />
                <Grade label="가격↑·RSI↓" color="rose" score="베어리시" desc="추세 약화. P5 페널티 -3" />
              </tbody>
            </table>
          </Section>

          {/* 5. 거래량 비율 */}
          <Section title="거래량 비율" emoji="📈">
            <p>전일 거래량 ÷ 직전 20일 평균 거래량.</p>
            <table className="w-full text-xs">
              <tbody>
                <SimpleRow left="≥ 2.0×" desc="강한 시장 관심. B2 만점, P1 면제" />
                <SimpleRow left="1.3 ~ 2.0×" desc="평소보다 활발. B2 +2점" />
                <SimpleRow left="0.7 ~ 1.3×" desc="평범. 가산 없음" />
                <SimpleRow left="< 0.7×" desc="시장 무관심. P1 페널티 -3" />
              </tbody>
            </table>
          </Section>

          {/* 6. 가격표 */}
          <Section title="진입가·손절가·목표가 (R-multiple)" emoji="🎯">
            <p>1R = 진입가 − 손절가 (위험 한도).</p>
            <Bullets>
              <li><strong>진입가 (피벗)</strong> = max(전일 고점, 프리마켓 고점)</li>
              <li><strong>손절가</strong> = max(VWAP, 컨솔 저점, 전일 종가) 중 피벗에 가장 가까운 값. 피벗 대비 ≥ 1.5% 보장</li>
              <li><strong>1차 목표 (1R)</strong> = 진입가 + 1R</li>
              <li><strong>2차 목표 (2R)</strong> = 진입가 + 2R</li>
              <li><strong>주식수</strong> = ⌊(자본 × 0.5%) ÷ 1R⌋ × Regime 사이즈 배수</li>
              <li><strong>위험비율</strong> = 1R / 진입가 (5% 초과면 변동성 종목)</li>
            </Bullets>
            <Note>
              <strong>운영 룰</strong>: 1R 도달 시 1/3~1/2 분할 익절, 2R 추가 익절, 나머지는 VWAP/9EMA 트레일링 스탑.
            </Note>
          </Section>

          {/* 7. 5-Block */}
          <Section title="5-Block 점수표 (100점)" emoji="🧮">
            <p>카드 하단의 색상 막대가 각 블록의 점수.</p>

            <Block title="Block 0 — 시장 체력 (Regime, 15)" color="회색" desc="시장 분위기. 종목 무관, 전 종목 동일.">
              <ul className="ml-4 list-disc text-[11px] space-y-0.5">
                <li>SPY &gt; 20EMA (+2)</li>
                <li>SPY 20EMA &gt; 50EMA (+2)</li>
                <li>QQQ &gt; 20EMA (+2)</li>
                <li>IWM 10일 RS vs SPY 양수 (+2) — 소형주 강세</li>
                <li>VIX &lt; 20 (+2) — 변동성 안정</li>
                <li>NYSE A/D Line 5일 상승 (+2) — breadth</li>
                <li>최근 10일 내 Follow-Through Day (+3)</li>
              </ul>
              <p className="mt-1 text-[11px]">
                모드: <strong className="text-emerald-600">12~15 공격</strong> /{" "}
                <strong className="text-amber-600">7~11 중립 (×0.7 size)</strong> /{" "}
                <strong className="text-rose-600">0~6 방어 (long 차단)</strong>
              </p>
            </Block>

            <Block title="Block A — 추세·강도 (25)" color="파랑" desc="이미 강한 종목인지.">
              <ul className="ml-4 list-disc text-[11px] space-y-0.5">
                <li><strong>A1 RS Rating IBD-style (10)</strong> — 4-quarter weighted percentile</li>
                <li><strong>A2 Multi-TF MOM (8)</strong> — 1m/3m/6m RS vs SPY 모두 양수면 만점</li>
                <li><strong>A3 Stage 2 Trend Template (7)</strong> — Minervini 8조건 통과</li>
              </ul>
            </Block>

            <Block title="Block B — 재료·거래량 (20)" color="노랑" desc="오늘 움직일 트리거.">
              <ul className="ml-4 list-disc text-[11px] space-y-0.5">
                <li><strong>B1 RVOL (8)</strong> — min(8, 1.5 × log₂(RVOL+1))</li>
                <li><strong>B2 거래량 surge (4)</strong> — 일봉 ≥2× 만점, 1.3~2× 2점</li>
                <li><strong>B3 카탈리스트 (8)</strong> — ER=8, FDA·M&A=6, 업그레이드=4, 일반뉴스=1</li>
              </ul>
            </Block>

            <Block title="Block C — 셋업 품질 (25)" color="초록" desc="진입 타이밍의 질.">
              <ul className="ml-4 list-disc text-[11px] space-y-0.5">
                <li><strong>C1 Pattern (6)</strong> — tight_flag=6, Bull Flag=5, 20일 신고가=4</li>
                <li><strong>C2 Pivot 근접 (4)</strong> — ±0.5%=4, 0.5~2%=3, 2~5%=1</li>
                <li><strong>C3 Base 기간 (4)</strong> — NR 컨솔 3~20일 만점</li>
                <li><strong>C4 Open Location (5)</strong> — 전일 range 위(+2), 피벗 위(+2), VWAP reclaim(+1)</li>
                <li><strong>C5 Compression/Expansion (6)</strong> — VCP 본질, 둘 다 = 골든</li>
              </ul>
            </Block>

            <Block title="Block D — 리스크 (15)" color="주황" desc="진입 후 견디기 좋은가.">
              <ul className="ml-4 list-disc text-[11px] space-y-0.5">
                <li><strong>D1 RSI 구조 (5)</strong> — higher low + 50~75 = 5점, super leader = 2점</li>
                <li><strong>D2 Beta sweet spot (3)</strong> — 1.0~2.0 만점</li>
                <li><strong>D3 ATR-RR (3)</strong> — (피벗 − 진입) ÷ ATR(14): ≥3 만점</li>
                <li><strong>D4 섹터 강도 (4)</strong> — ETF 동방향 + 강한 갭 = 4점</li>
              </ul>
            </Block>

            <Block title="페널티 (-15까지)" color="빨강" desc="감점 항목.">
              <ul className="ml-4 list-disc text-[11px] space-y-0.5">
                <li><strong>P1 거래량 부족</strong> -3 — vol_ratio &lt; 0.7</li>
                <li><strong>P2 Climax</strong> -4 — RSI 85+ AND 거래량 폭증</li>
                <li><strong>P3 Squeeze</strong> -3 — Phase 2 (데이터 미연결)</li>
                <li><strong>P4 Pivot extended</strong> -3 — 피벗 +5% 이상</li>
                <li><strong>P5 RSI 구조 위반</strong> -3 — divergence 또는 climax</li>
                <li><strong>P6 Open Location 위험</strong> -2 — 시초가가 전일 저점 아래</li>
              </ul>
            </Block>
          </Section>

          {/* 8. 보유 기간 */}
          <Section title="보유 기간 (스윙 / 단타)" emoji="⏱️">
            <table className="w-full text-xs">
              <tbody>
                <SimpleRow left="스윙 (default)" desc="ATR < 5% OR Day 모드 OFF. 2~5일 보유, MA·RS 기반" />
                <SimpleRow left="단타 (명시 토글)" desc="ATR ≥ 5%. 당일 청산, 변동성 큰 종목" />
              </tbody>
            </table>
            <Note>
              <strong>Core = 스윙, Satellite = 단타</strong>. yfinance 데이터 한계상 단타는 검증 후 활성.
            </Note>
          </Section>

          {/* 9. 시간대 */}
          <Section title="자동 운영 스케줄 (US Eastern)" emoji="🕐">
            <p className="mb-2 text-xs text-zinc-600 dark:text-zinc-400">
              사용자 거주지(뉴저지)와 동일 시간대. 모든 시간은 동부 표준시(ET) 기준.
            </p>
            <table className="w-full text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="px-2 py-1 text-left">시간 (ET)</th>
                  <th className="px-2 py-1 text-left">이벤트</th>
                </tr>
              </thead>
              <tbody>
                <SimpleRow left="04:00" desc="프리마켓 시작 (얇은 거래)" />
                <SimpleRow left="07:00" desc="유럽 기관 진입 시작" />
                <SimpleRow left="08:55" desc="⚙ Stage 2 picks 자동 산출 (cron)" />
                <SimpleRow left="09:00" desc="★ 프리마켓 데이터 90%+ 누적" />
                <SimpleRow left="09:15" desc="📧 데일리 이메일 발송 (regime + picks + 전일 PnL)" />
                <SimpleRow left="09:25" desc="⚙ 3 시스템 비교 picks 로그" />
                <SimpleRow left="09:30" desc="📈 정규장 개장" />
                <SimpleRow left="16:00" desc="정규장 마감" />
                <SimpleRow left="16:30" desc="⚙ Outcome backfill (1d)" />
                <SimpleRow left="16:35" desc="⚙ Comparison backfill (1d/5d/10d)" />
              </tbody>
            </table>
            <Note>
              장 마감 후(16:00 ET 이후 ~ 04:00 ET 다음날) 실행 시 yfinance 프리마켓 데이터 비어있음 →
              자동 lenient 모드 (점수 컷 60→40 완화).
            </Note>
          </Section>

          {/* 10. FAQ */}
          <Section title="자주 묻는 질문" emoji="❓">
            <Faq q="다시 선정해도 같은 종목이 나오는데?">
              시스템이 결정론적입니다. 같은 시장 데이터 → 같은 결과. 데이터 갱신(다음 거래일/장중)되어야 변합니다.
            </Faq>
            <Faq q="공격모드인데 picks가 너무 적어요">
              30종목 universe에서 Stage 2 + 컷 60점 통과 종목이 적기 때문. 의도된 보수 동작 — "고를 게 없으면 안 사는 게 정답".
            </Faq>
            <Faq q="방어모드일 때는 어떻게 되나요?">
              Regime score 0~6일 때 모든 long 차단. picks = 0. 시장 회복 전까지 cash hold.
            </Faq>
            <Faq q="실제 매매 자동 실행되나요?">
              아직 paper 단계. picks는 "제안"만, 실제 주문은 사용자가 Webull에서 직접. 자동 주문은 Phase 3 + Cash T+1 정산 모듈 함께.
            </Faq>
            <Faq q="등급 D는 왜 안 보이나요?">
              컷 통과 못하면 DB 저장 안 함. lenient 40점 컷에서도 미달이면 picks 자체가 비어있음.
            </Faq>
            <Faq q="오늘의 종목 스캐너와 어떻게 다른가요?">
              <strong>스캐너</strong>가 코어 시스템 (검증된 알파 OOS Sharpe 3.01). 이 v3 Daily Picks는 30종목 좁은 universe + 100점 정밀 표 — 이론 종합이지만 백테스트 미검증. 의사결정은 스캐너 기준, 이건 보조/심화 분석.
            </Faq>
          </Section>
        </div>
      </aside>
    </>
  );
}

/* ─────────── 보조 컴포넌트 ─────────── */

function Section({
  title,
  emoji,
  children,
}: {
  title: string;
  emoji: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6 border-b border-zinc-100 pb-5 last:border-b-0 dark:border-zinc-800">
      <h3 className="mb-2 text-base font-semibold text-zinc-900 dark:text-zinc-50">
        <span className="mr-2">{emoji}</span>
        {title}
      </h3>
      <div className="space-y-2 text-xs leading-relaxed text-zinc-700 dark:text-zinc-300">
        {children}
      </div>
    </section>
  );
}

function Bullets({ children }: { children: React.ReactNode }) {
  return <ul className="ml-1 list-disc space-y-1 pl-4">{children}</ul>;
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded border border-blue-200 bg-blue-50 p-2 text-[11px] text-blue-900 dark:border-blue-900 dark:bg-blue-950/50 dark:text-blue-200">
      💡 {children}
    </div>
  );
}

const COLOR_BG: Record<string, string> = {
  emerald: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  blue: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  rose: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  zinc: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

function Grade({
  label,
  color,
  score,
  desc,
}: {
  label: string;
  color: string;
  score: string;
  desc: string;
}) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5">
        <span className={`rounded px-1.5 py-0.5 text-xs font-bold ${COLOR_BG[color] ?? COLOR_BG.zinc}`}>
          {label}
        </span>
      </td>
      <td className="px-2 py-1.5 font-medium">{score}</td>
      <td className="px-2 py-1.5 text-[11px] text-zinc-600 dark:text-zinc-400">{desc}</td>
    </tr>
  );
}

function SimpleRow({ left, desc }: { left: string; desc: string }) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5 font-mono text-[11px] text-zinc-700 dark:text-zinc-300">{left}</td>
      <td className="px-2 py-1.5 text-[11px] text-zinc-600 dark:text-zinc-400">{desc}</td>
    </tr>
  );
}

function Row3({ a, b, c }: { a: string; b: string; c: string }) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5 text-[11px]">{a}</td>
      <td className="px-2 py-1.5 font-mono text-[11px]">{b}</td>
      <td className="px-2 py-1.5 font-mono text-[11px]">{c}</td>
    </tr>
  );
}

function Reason({
  label,
  detail,
  warning = false,
}: {
  label: string;
  detail: string;
  warning?: boolean;
}) {
  return (
    <div className="flex items-start gap-2 text-[11px]">
      <span className={warning ? "text-amber-600 dark:text-amber-400" : "text-emerald-600 dark:text-emerald-400"}>
        {warning ? "⚠" : "▸"}
      </span>
      <div>
        <strong className="text-zinc-900 dark:text-zinc-100">{label}</strong>
        <span className="ml-1 text-zinc-600 dark:text-zinc-400">— {detail}</span>
      </div>
    </div>
  );
}

function Block({
  title,
  color,
  desc,
  children,
}: {
  title: string;
  color: string;
  desc: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-3 rounded border border-zinc-200 bg-zinc-50 p-2 dark:border-zinc-800 dark:bg-zinc-900/50">
      <h4 className="text-xs font-semibold text-zinc-900 dark:text-zinc-50">
        <span className="mr-1 text-[10px] text-zinc-500">[{color}]</span>
        {title}
      </h4>
      <p className="mt-0.5 mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">{desc}</p>
      {children}
    </div>
  );
}

function Faq({ q, children }: { q: string; children: React.ReactNode }) {
  return (
    <details className="rounded border border-zinc-200 p-2 dark:border-zinc-800">
      <summary className="cursor-pointer text-xs font-medium text-zinc-900 dark:text-zinc-50">
        Q. {q}
      </summary>
      <p className="mt-2 pl-3 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
        {children}
      </p>
    </details>
  );
}
