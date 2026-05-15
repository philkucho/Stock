"use client";

/**
 * 통합 대시보드용 우측 슬라이드 도움말 드로어.
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
        aria-label="통합 대시보드 도움말"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
          <div>
            <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-50">
              📖 통합 대시보드 도움말
            </h2>
            <p className="text-[11px] text-zinc-500">시그널 검증 + 운용 정보 + Tier 분류</p>
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
          {/* 1. 대시보드의 의미 */}
          <Section title="이 화면은 무엇인가" emoji="🎯">
            <p>두 시스템의 강점을 합친 통합 뷰:</p>
            <Bullets>
              <li>
                <strong>시그널 검증</strong> — 6개 모멘텀 시그널 + 시장 상태(regime) +
                실적 단계(PEAD) + 백테스트 검증 화이트리스트
              </li>
              <li>
                <strong>운용 정보</strong> — 자동 계산된 진입가/손절가/1R/2R 목표가 + 주식 수
              </li>
              <li>
                <strong>Tier 분류</strong> — 시그널 강도 + 백테스트 검증 + 실적 위험 가중
              </li>
            </Bullets>
            <Note>
              <strong>스캐너</strong>가 코어 시그널, <strong>Picks</strong>가 정밀 점수표,
              여기는 둘을 묶어 <strong>운용에 바로 쓸 수 있는 형태</strong>로 정리.
            </Note>
          </Section>

          {/* 2. Tier 분류 */}
          <Section title="Tier 분류 (S / A / B / C)" emoji="🏅">
            <p>각 후보는 종합 평가로 Tier에 배정.</p>
            <table className="w-full text-xs">
              <tbody>
                <Tier
                  label="S"
                  color="emerald"
                  emoji="🥇"
                  desc="최고 신뢰도 — 시그널 5+ AND 백테스트 적중 ≥ 70% AND 실적 위험 없음"
                />
                <Tier
                  label="A"
                  color="blue"
                  emoji="🥈"
                  desc="강한 후보 — 시그널 4+ AND 백테스트 검증 OR PEAD 알파"
                />
                <Tier
                  label="B"
                  color="amber"
                  emoji="🥉"
                  desc="보조 후보 — 시그널 3+ AND 검증 종목(WHITELIST)"
                />
                <Tier
                  label="C"
                  color="zinc"
                  emoji="👀"
                  desc="관찰 — 시그널 약하거나 검증 부족. 펼쳐 보기로 토글"
                />
              </tbody>
            </table>
            <Note>
              상단 카운터는 각 Tier의 후보 수. 실제 매수 후보는 <strong>S → A → B</strong> 순으로 채택.
            </Note>
          </Section>

          {/* 3. 시장 상태 (Regime) */}
          <Section title="시장 상태 (Regime)" emoji="🌐">
            <p>시장이 매수에 우호적인지 종목 무관하게 판단.</p>
            <table className="w-full text-xs">
              <tbody>
                <SimpleRow
                  left="🟢 진입 가능"
                  desc="S&P 500(SPY)이 200일선 위 + 변동성지수(VIX) 안정 — 정상 진입"
                />
                <SimpleRow
                  left="🛑 진입 차단"
                  desc="SPY 200일선 아래 OR VIX 급등 — 신규 long 차단, cash hold"
                />
              </tbody>
            </table>
            <Note>
              <strong>SPY</strong>(S&P 500 ETF)의 200일선과 <strong>VIX</strong>(변동성지수)로
              체크. 차단 상태에서는 후보가 있어도 매수하지 않는 것이 원칙.
            </Note>
          </Section>

          {/* 4. 실적 단계 (PEAD) */}
          <Section title="실적 단계 (PEAD)" emoji="📰">
            <p>실적 발표 시점 기준 3가지 단계로 분류.</p>
            <table className="w-full text-xs">
              <tbody>
                <Phase
                  label="실적 임박"
                  color="rose"
                  emoji="🚨"
                  desc="발표 ±5일 이내, 발표 전 — 갭 위험 大. 기본값에서 차단"
                />
                <Phase
                  label="실적 직후 (PEAD)"
                  color="amber"
                  emoji="📈"
                  desc="발표 후 5일 이내 — Post Earnings Announcement Drift 알파 +2.56% 검증"
                />
                <Phase
                  label="재료 없음"
                  color="emerald"
                  emoji="✅"
                  desc="실적 윈도우 밖 — 일반 신호로 진입"
                />
              </tbody>
            </table>
            <Note>
              <strong>설정 패널의 "실적 처리"</strong>:{" "}
              <strong>실적 임박만 차단(권장)</strong> → PEAD는 허용,{" "}
              <strong>모두 차단</strong> → 실적 ±5일 전부 제외,{" "}
              <strong>차단 없음</strong> → 실적 무시.
            </Note>
          </Section>

          {/* 5. 5차원 점수 */}
          <Section title="시그널 점수 (5차원)" emoji="🧮">
            <p>카드의 색상 막대가 각 항목의 점수.</p>
            <Block title="추세 정렬 (최대 2점)" color="파랑" desc="MA·종가가 우상향으로 정렬되어 있는가." />
            <Block title="모멘텀 (최대 2점)" color="보라" desc="다중 기간(1m/3m) 상대강도가 시장보다 강한가." />
            <Block title="거래량 (최대 1점)" color="초록" desc="평균 대비 오늘 거래량(상대거래량)이 1.5배 이상인가." />
            <Block title="돌파 (최대 1점)" color="주황" desc="최근 고점(20일/52주) 돌파 또는 근접." />
            <Block title="감점 (최대 −3)" color="빨강" desc="과열·climax·전일 저점 이탈 등 위험 패턴 발견 시." />
            <Note>
              총점은 <strong>최대 6점 + 페널티</strong>. 설정 패널의{" "}
              <strong>"최소 점수"</strong>로 컷오프 조절 (기본 2 이상).
            </Note>
          </Section>

          {/* 6. 진입가·손절가·목표가 */}
          <Section title="진입가·손절가·목표가 (R-multiple)" emoji="🎯">
            <p>
              <strong>1R</strong> = 진입가 − 손절가 (위험 한도 1단위).
            </p>
            <Bullets>
              <li>
                <strong>진입가</strong> — 종가 기준 (장중 매수 시 시초·VWAP 위 confirm 권장)
              </li>
              <li>
                <strong>손절가</strong> — 진입가 −{" "}
                <span className="font-mono">ATR(14) × 배수</span>. 빨간색 강조
              </li>
              <li>
                <strong>1차 목표 (1R)</strong> — 진입가 + 1R, 분할 익절 추천
              </li>
              <li>
                <strong>2차 목표 (2R)</strong> — 진입가 + 2R, 잔여 익절 또는 트레일링
              </li>
              <li>
                <strong>주식수</strong> = ⌊(자본 × 위험%) ÷ 주당 위험⌋
              </li>
              <li>
                <strong>주당 위험</strong> = 진입가 − 손절가 (1R과 같음)
              </li>
              <li>
                <strong>계좌 위험</strong> = 주식수 × 주당 위험 (실제 손실 한도)
              </li>
            </Bullets>
            <Note>
              <strong>운영 룰</strong>: 1R 도달 시 1/3~1/2 분할 익절, 2R에서 추가 익절,
              나머지는 9EMA / VWAP 트레일링 스탑.
            </Note>
          </Section>

          {/* 7. ATR 손절 거리 */}
          <Section title="손절 거리 (ATR 배수)" emoji="📏">
            <p>
              <strong>ATR(14)</strong>(평균 진폭 14일)로 종목 변동성에 비례한 손절을 자동 설정.
            </p>
            <table className="w-full text-xs">
              <tbody>
                <SimpleRow
                  left="1.0~1.4× (타이트)"
                  desc="휩쏘 위험↑, 주식수↑. 정확한 진입에 자신 있을 때만"
                />
                <SimpleRow
                  left="1.5~2.2× (표준)"
                  desc="균형. 기본값 2.0× 권장"
                />
                <SimpleRow
                  left="2.3~2.8× (넉넉)"
                  desc="장기보유, 큰 변동 허용, 주식수↓"
                />
                <SimpleRow
                  left="2.9~3.5× (매우 넉넉)"
                  desc="고변동성 종목용. 같은 계좌 위험에서 매우 작은 포지션"
                />
              </tbody>
            </table>
            <Note>
              배수가 작을수록 <strong>주당 위험이 작아져 주식 수가 늘어나고</strong>,
              크게 하면 그 반대. <strong>총 계좌 위험은 동일</strong>(설정한 위험% 그대로).
            </Note>
          </Section>

          {/* 8. 백테스트 검증 */}
          <Section title="과거 백테스트 검증" emoji="📊">
            <p>같은 시그널이 과거에 어떤 성과를 냈는지.</p>
            <Bullets>
              <li>
                <strong>적중률 (hit rate)</strong> — 5일 후 흑자로 끝난 비율. 70% 이상 강조
              </li>
              <li>
                <strong>표본 수 (n)</strong> — 검증 거래 횟수. 30회 이상이 신뢰 구간
              </li>
              <li>
                <strong>평균 수익 (avg_ret)</strong> — 5일 평균 수익률.{" "}
                <span className="font-mono">+2%</span>를 강한 알파 기준
              </li>
            </Bullets>
            <Note>
              녹색 박스로 표시된 카드는 <strong>백테스트 검증 통과</strong>. Tier S/A 등급에 가산.
            </Note>
          </Section>

          {/* 9. 왜 이 종목인가 — 이유 라벨 */}
          <Section title="왜 이 종목인가? — 이유 라벨" emoji="🧩">
            <p>
              <span className="text-emerald-600">▲</span> 긍정 /{" "}
              <span className="text-rose-600">▼</span> 부정 /{" "}
              <span className="text-zinc-500">•</span> 중립
            </p>
            <Reason
              label="강한 추세 정렬"
              detail="단·중·장기 이동평균이 우상향 정렬 — 매수 우호"
            />
            <Reason
              label="다중 기간 모멘텀"
              detail="1개월·3개월 상대강도 모두 시장 평균 초과"
            />
            <Reason
              label="거래량 폭증"
              detail="평소 거래량 대비 1.5배 이상 — 시장 관심 집중"
            />
            <Reason
              label="신고가 돌파"
              detail="최근 20일 또는 52주 고점 돌파/근접"
            />
            <Reason
              label="검증 종목"
              detail="WHITELIST — 백테스트 알파 입증된 종목군"
            />
            <Reason
              label="PEAD 윈도우"
              detail="실적 발표 직후 5일 — 사후 표류 알파 윈도우"
            />
            <Reason
              label="과열 위험"
              detail="RSI 85+ 또는 climax 거래량 — 단기 정점 가능"
              warning
            />
            <Reason
              label="실적 임박"
              detail="발표 ±5일 — 갭 위험으로 기본 차단"
              warning
            />
          </Section>

          {/* 10. 설정 패널 */}
          <Section title="설정 패널 사용법" emoji="⚙️">
            <Bullets>
              <li>
                <strong>계좌 자본</strong> — 매매에 쓸 계좌 잔액 (USD)
              </li>
              <li>
                <strong>트레이드당 위험</strong> — 한 번에 잃어도 되는 비율 (기본 0.5%, 권장 0.3~1%)
              </li>
              <li>
                <strong>최소 점수</strong> — 후보로 인정할 시그널 점수 컷 (기본 2)
              </li>
              <li>
                <strong>실적 처리</strong> — 실적 윈도우 종목 처리 (위 §4 참고)
              </li>
              <li>
                <strong>손절 거리 (ATR 배수)</strong> — 손절 폭 조절 (위 §7 참고)
              </li>
            </Bullets>
            <Note>
              설정 변경 시 <strong>자동 재조회</strong>. 후보가 너무 적으면 점수 컷을
              낮추거나 ATR 배수를 키워 진입 폭을 넓혀볼 것.
            </Note>
          </Section>

          {/* 11. 시간대 */}
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
                <SimpleRow left="08:55" desc="⚙ Stage 2 picks 자동 산출" />
                <SimpleRow left="09:00" desc="★ 프리마켓 데이터 90%+ 누적" />
                <SimpleRow left="09:15" desc="📧 데일리 이메일 발송" />
                <SimpleRow left="09:25" desc="⚙ 3 시스템 비교 picks 로그" />
                <SimpleRow left="09:30" desc="📈 정규장 개장" />
                <SimpleRow left="16:00" desc="정규장 마감" />
                <SimpleRow left="16:30" desc="⚙ Outcome backfill (1d)" />
                <SimpleRow left="16:35" desc="⚙ Comparison backfill (1d/5d/10d)" />
              </tbody>
            </table>
          </Section>

          {/* 12. FAQ */}
          <Section title="자주 묻는 질문" emoji="❓">
            <Faq q="Tier S가 비어있는데?">
              매우 높은 컷(시그널 5+, 적중률 70%+, 실적 위험 없음)이라 평소에도 자주 비어있음.
              비어있다는 건 의도된 보수 동작 — 더 약한 후보로 무리하게 진입하지 않는 것이 안전.
            </Faq>
            <Faq q="시장 상태가 '진입 차단'이면 어떻게?">
              S&P 500이 200일선 아래거나 변동성이 급등한 상태. 후보가 있어도 신규 매수는 보류,
              현금 비중을 늘리는 것을 권장. 시장 회복(SPY 200일선 재돌파) 전까지 cash hold.
            </Faq>
            <Faq q="실적 직후(PEAD) 진입은 안전한가?">
              백테스트상 발표 후 5일 PEAD 윈도우는 일반 신호 대비 +2.56% 추가 알파 확인됨.
              단, <strong>발표 전</strong> 진입은 갭 위험이 크므로 기본 차단.
            </Faq>
            <Faq q="주식 수가 0으로 나오는데?">
              계좌 위험(자본 × 위험%) 대비 주당 위험이 너무 큰 경우. ATR 배수를 줄이거나
              자본·위험% 설정을 늘려야 1주 이상 매수 가능.
            </Faq>
            <Faq q="실제 매매 자동 실행되나요?">
              아직 모의투자(paper) 단계. 후보는 "제안"만, 실제 주문은 사용자가 Webull에서 직접.
              자동 주문은 추후 실거래 인증 + 정산 모듈과 함께 활성화.
            </Faq>
            <Faq q="picks 페이지와 어떻게 다른가요?">
              <strong>picks</strong>는 30종목 좁은 universe + 100점 정밀 표 (이론 종합).{" "}
              <strong>이 화면</strong>은 백테스트 검증된 시그널(스캐너) + 운용 정보 + Tier 분류로
              실전 의사결정에 더 가깝다.
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

function Tier({
  label,
  color,
  emoji,
  desc,
}: {
  label: string;
  color: string;
  emoji: string;
  desc: string;
}) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5">
        <span className={`rounded px-1.5 py-0.5 text-xs font-bold ${COLOR_BG[color] ?? COLOR_BG.zinc}`}>
          {emoji} {label}
        </span>
      </td>
      <td className="px-2 py-1.5 text-[11px] text-zinc-600 dark:text-zinc-400">{desc}</td>
    </tr>
  );
}

function Phase({
  label,
  color,
  emoji,
  desc,
}: {
  label: string;
  color: string;
  emoji: string;
  desc: string;
}) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5 whitespace-nowrap">
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${COLOR_BG[color] ?? COLOR_BG.zinc}`}>
          {emoji} {label}
        </span>
      </td>
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
}: {
  title: string;
  color: string;
  desc: string;
}) {
  return (
    <div className="mt-3 rounded border border-zinc-200 bg-zinc-50 p-2 dark:border-zinc-800 dark:bg-zinc-900/50">
      <h4 className="text-xs font-semibold text-zinc-900 dark:text-zinc-50">
        <span className="mr-1 text-[10px] text-zinc-500">[{color}]</span>
        {title}
      </h4>
      <p className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">{desc}</p>
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
