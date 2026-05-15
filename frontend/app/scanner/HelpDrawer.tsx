"use client";

/**
 * 우측 슬라이드 드로어 — 데이터를 보면서 도움말을 동시에 읽기 위한 비차단형 패널.
 * - ESC 또는 ✕ 클릭으로 닫기
 * - backdrop 없음 (메인 데이터 그대로 보이게)
 * - max-w-md 폭 (약 28rem)
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
      {/* 매우 옅은 backdrop — 시각적 분리만, 데이터 가독은 유지 */}
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
        aria-label="스캐너 도움말"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
          <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-50">
            📖 스캐너 도움말
          </h2>
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

        <div className="h-[calc(100vh-4rem)] overflow-y-auto p-5 text-sm text-zinc-700 dark:text-zinc-300">
          {/* 0. 시스템 한 줄 */}
          <Section title="이 시스템은 무엇인가" emoji="🎯">
            <p>
              <strong>122종목 화이트리스트 × 6개 단순 시그널 (Score≥4)</strong> 시스템.
              2024-06+ 12개월 데이터로 빌드, 2025-01+ 표본에서 <strong>OOS Sharpe 3.01</strong> 검증 완료.
            </p>
            <p className="mt-2">
              평균 5일 수익률: <strong className="text-emerald-600">+1.41% (clean)</strong>,{" "}
              <strong className="text-amber-700 dark:text-amber-400">+2.56% (PEAD)</strong>.
            </p>
          </Section>

          {/* 1. 시장 상태 */}
          <Section title="시장 상태 (Regime Gate)" emoji="🟢">
            <p>화면 좌상단 카드. 진입을 허용할지 차단할지 결정하는 1차 게이트.</p>
            <Bullets>
              <li>
                <Badge color="emerald">🟢 진입 가능</Badge>{" "}
                S&P 500이 200일선 위 + VIX &lt; 25 — 모든 후보 진입 OK
              </li>
              <li>
                <Badge color="rose">🛑 진입 차단</Badge>{" "}
                S&P 500이 200일선 아래 OR VIX ≥ 25 — long 진입 모두 보류
              </li>
            </Bullets>
            <Note>
              200일선은 장기 추세의 표준. VIX(공포지수)는 변동성. 둘 다 무너지면 베어마켓 신호 → 시스템 자동 OFF.
            </Note>
          </Section>

          {/* 2. 기준 일자 / 화이트리스트 */}
          <Section title="기준 일자 / 화이트리스트" emoji="📅">
            <p>두 번째 카드. <strong>기준 일자</strong>는 화면 데이터의 일봉 마감일.</p>
            <p className="mt-2">
              <strong>화이트리스트 122종목</strong>은 백테스트로 검증된 종목 풀:
            </p>
            <Bullets>
              <li>최근 12개월 표본에서 hit rate ≥ 55%, 평균 +0.5% NET 이상</li>
              <li>거래 표본 n ≥ 8 (통계적 유효성)</li>
              <li>regime gate ON 필터 적용된 결과만</li>
              <li>3-6개월마다 재구축 (시장 사이클 변화 반영)</li>
            </Bullets>
          </Section>

          {/* 3. 실적 분류 */}
          <Section title="실적 분류 (PEAD 인지)" emoji="📈">
            <p>세 번째 카드. 종목별 실적 발표 시점에 따른 분류:</p>
            <Bullets>
              <li>
                <Badge color="rose">🚨 임박</Badge>{" "}
                5일 이내 실적 발표 예정 — <strong>진입 보류</strong> (binary risk)
              </li>
              <li>
                <Badge color="amber">📈 직후</Badge>{" "}
                최근 실적 발표 후 ~5일 — <strong>PEAD 알파 +2.56%/5d</strong>
              </li>
              <li>
                <Badge color="emerald">✅ 재료없음</Badge>{" "}
                실적 영향 없음 — 순수 모멘텀 알파 +1.41%/5d
              </li>
            </Bullets>
            <Note>
              <strong>PEAD (Post-Earnings Announcement Drift)</strong>: 실적 발표 후 며칠~수주간
              가격 상승이 지속되는 학계 검증된 anomaly. 통념과 달리 "발표 후" 진입이 알파 1.8배 강함.
            </Note>
          </Section>

          {/* 4. 데이터 현황 */}
          <Section title="데이터 현황" emoji="📦">
            <p>우상단 카드. 현재 시스템이 보유한 raw 데이터:</p>
            <Bullets>
              <li><strong>일봉 X종목</strong> — yfinance에서 적재된 OHLCV</li>
              <li><strong>실적 캘린더 X종목</strong> — 다음/직전 실적 발표일 정보</li>
            </Bullets>
            <Note>
              화이트리스트(122)보다 일봉(518)이 큰 이유: scan은 화이트리스트만 평가하지만 일봉은
              S&P 500 전체를 보유 (재구축 시 후보 풀로 사용).
            </Note>
          </Section>

          {/* 5. 필터 */}
          <Section title="필터 옵션" emoji="🎛️">
            <Filter
              label="최소 점수"
              desc="6개 시그널 합계 컷오프. 4 이상 권장 (OOS Sharpe 검증된 임계값)."
              opts={[
                ["1", "느슨 — 후보 많음, false positive ↑"],
                ["4", "권장 — 검증된 알파 임계값"],
                ["5", "엄격 — 만점 종목만"],
              ]}
            />
            <Filter
              label="실적 분류 모드"
              desc="실적 캘린더 활용 방식."
              opts={[
                ["실적 임박만 차단 (PEAD 허용, 권장)", "발표 전 5일만 차단, 발표 후 진입 OK — 알파 최대화"],
                ["실적 ±5일 모두 차단 (보수적)", "이전 v1 동작 — PEAD 알파 0.85% 손실하지만 안전"],
                ["차단 없음", "실적 페이즈 무시 — 디버깅 용도"],
              ]}
            />
            <Filter
              label="실적 단계 필터"
              desc="화면 표시 종목을 단계별로 한정."
              opts={[
                ["전체", "모든 분류 표시"],
                ["재료없음만", "순수 모멘텀 후보만"],
                ["실적 직후 (PEAD)만", "PEAD 알파만 보고싶을 때"],
              ]}
            />
            <Filter
              label="섹터"
              desc="GICS 섹터별 필터링. 섹터 rotation 시 특정 섹터만 보고 싶을 때."
            />
          </Section>

          {/* 6. 테이블 컬럼 */}
          <Section title="후보 테이블 컬럼" emoji="📋">
            <Col label="#" desc="해당 시점 점수 순위 (총점 같으면 거래량 순)" />
            <Col label="종목" desc="티커 + GICS 섹터" />
            <Col label="실적" desc="🚨/📈/✅ 분류 + 다음 실적일 (D±N일)" />
            <Col label="종가" desc="기준 일자의 일봉 종가 ($)" />
            <Col
              label="거래량"
              desc="20일 평균 대비 비율. 1.5× 이상 강조 (초록 굵게). 전일 거래량 ÷ avg(20일)"
            />
            <Col label="점수" desc="6개 시그널 합계 (-2 ~ +6 가능, 5점 만점). 색상: 5점=진초록, 4점=초록, 3점=파랑, 그 이하 회색" />
            <Col label="시그널" desc="6개 개별 신호 (다음 섹션 상세). +1=양, 0=중립(·), -1=음" />
            <Col
              label="과거 통계"
              desc="해당 종목이 과거 같은 score에서 5일 후 어떻게 됐는지. hit% = 양수 수익 비율, 평균 = 평균 수익률, n = 표본 수"
            />
          </Section>

          {/* 7. 시그널 6개 상세 */}
          <Section title="6개 시그널 상세" emoji="🔬">
            <Sig
              short="VT"
              ko="거래량 추세"
              detail="1주/1달 평균 ≥ 1.2배 + 전일/1달 평균 ≥ 1.5배. 단순 거래량 폭증이 아니라 '추세적' 증가."
            />
            <Sig
              short="MA"
              ko="이동평균 정배열"
              detail="5일 SMA > 20일 SMA > 60일 SMA. 단기·중기·장기 추세가 모두 상향."
            />
            <Sig
              short="RSI"
              ko="상승 모멘텀"
              detail="RSI(14) 55~70 + 어제보다 RSI 상승. 과매수(70+) 아닌 강세 영역."
            />
            <Sig
              short="MAC"
              ko="MACD 전환"
              detail="MACD 히스토그램 부호 변화. +1=음→양 전환, -1=양→음 전환. 추세 변화 신호."
            />
            <Sig
              short="M200"
              ko="200일선 위/아래"
              detail="가격이 200일 SMA 위. 장기 추세 필터."
            />
            <Sig
              short="BRK"
              ko="20일 신고가 돌파"
              detail="종가가 직전 20일 최고가 초과. 단기 박스 돌파 신호."
            />
            <Note>
              6개 모두 작동하면 +6 (이론적 최대), 보통 4점부터 의미있는 알파. 음수(-1) 시그널은
              단순 합산에서 차감 — 5개 양 + 1개 음 = 4점.
            </Note>
          </Section>

          {/* 8. 점수 해석 */}
          <Section title="점수 해석" emoji="🏅">
            <table className="w-full text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="px-2 py-1 text-left">점수</th>
                  <th className="px-2 py-1 text-left">의미</th>
                  <th className="px-2 py-1 text-left">기대치</th>
                </tr>
              </thead>
              <tbody>
                <Row3 a="6" b="만점 (드묾)" c="강한 confluence — 진입 0순위" badge="emerald" />
                <Row3 a="5" b="우수" c="대부분 시그널 양" badge="emerald" />
                <Row3 a="4" b="권장 컷" c="OOS Sharpe 검증 임계값" badge="emerald-light" />
                <Row3 a="3" b="중립" c="알파 미약" badge="blue" />
                <Row3 a="≤2" b="부족" c="진입 비추천" badge="zinc" />
              </tbody>
            </table>
          </Section>

          {/* 9. 운영 룰 */}
          <Section title="운영 룰 (Phase 3 vol_targeted)" emoji="⚙️">
            <Bullets>
              <li><strong>진입</strong> — 시장 상태 🟢 + 점수 ≥ 4 + 실적 임박 아님</li>
              <li><strong>손절</strong> — 진입가 -5% 도달 시</li>
              <li><strong>익절</strong> — 진입가 +8% 도달 시 1차 청산</li>
              <li><strong>트레일링</strong> — 최고가 대비 -3% 후퇴 시 청산</li>
              <li><strong>최대 보유</strong> — 5거래일 (시그널 약화 전)</li>
            </Bullets>
            <Note>
              Phase 3 vol_targeted preset 결과: drawdown · cost · regime gate 모두 정직하게 반영한
              backtest에서 OOS Sharpe 3.01.
            </Note>
          </Section>

          {/* 10. 백테스트 검증 */}
          <Section title="백테스트 검증 결과" emoji="📊">
            <table className="w-full text-xs">
              <tbody>
                <Row2 a="In-sample (2024-06+ 빌드)" b="Sharpe 3.12 (n=301)" />
                <Row2 a="OOS (2025-01+ 검증)" b="Sharpe 3.01 (n=255) ✓" />
                <Row2 a="평균 5d 수익 (clean)" b="+1.41% NET, hit 65%" />
                <Row2 a="평균 5d 수익 (PEAD)" b="+2.56% NET, hit 70%" />
                <Row2 a="ALL 합산" b="+1.54%, Sharpe 2.11" />
              </tbody>
            </table>
            <Note>
              "긴 데이터 = robust" 가설은 틀림. v2 (4년 빌드, n≥15) 시도는 OOS Sharpe -0.40으로 실패 —
              regime별 winner를 평균이 희석. 짧은 최신 윈도우 + regime gate 조합이 forward에서 우월.
            </Note>
          </Section>

          {/* 11. FAQ */}
          <Section title="자주 묻는 질문" emoji="❓">
            <Faq q="왜 score 4 이상부터 권장인가요?">
              백테스트에서 score 1~3은 평균 수익률이 noise 수준 (벤치마크와 차이 없음). 4 이상에서만 통계적으로
              유의한 알파(+1.5% 이상)가 나옴. 5점은 더 강하지만 표본 n이 작아짐.
            </Faq>
            <Faq q="실적 직후(📈)가 정말 진입해도 되나요? 위험하지 않나요?">
              발표 후 ±5일 윈도우 240건 표본에서 평균 +2.56%, hit 70%. 학계의 PEAD anomaly는 1968년 첫 발견 후
              지금까지 작동. 단, 발표 직후 hot day(D=0)는 변동 큼 — D+1부터 안전. 손절 -5% 룰 필수.
            </Faq>
            <Faq q="화이트리스트는 왜 122종목만 있나요?">
              백테스트 통과 종목만 자동 채택. 메가캡(AAPL/MSFT/AMZN)은 단기 윈도우(2024-06+)에선 알파가 약해
              제외됐음. 시장 사이클 따라 재구축마다 변동 — 고정 BLACKLIST 아님.
            </Faq>
            <Faq q="시장 상태가 🛑 차단인 날엔 어떻게 하나요?">
              시스템이 자동으로 모든 long 진입을 보류. 사용자는 그 날 cash hold 또는 별도 hedge.
              regime gate 자체가 "베어마켓에서 안 사는 게 정답" 룰 구현.
            </Faq>
            <Faq q="과거 통계의 hit, 평균은 어떻게 계산되나요?">
              해당 종목이 과거 같은 score 점수를 받았던 모든 거래일 추출 → 5거래일 후 수익률 계산 →
              hit = 양수 수익 비율, 평균 = 단순 평균. n은 표본 수. n &lt; 8이면 신뢰도 약함.
            </Faq>
            <Faq q="v3 Daily Picks와 어떻게 다른가요?">
              <strong>이 스캐너</strong>가 코어 시스템입니다 (검증된 알파 Sharpe 3.01). v3 Daily Picks는
              30종목 좁은 universe + 100점 정밀 표 — 이론 종합이지만 백테스트 검증 안 됨. 의사결정은
              이 스캐너 기준으로 하고, v3는 보조용/심화 분석으로 사용.
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
  "emerald-light": "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
  blue: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  rose: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  zinc: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

function Badge({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span className={`mr-1 inline-flex items-center rounded px-1.5 py-0.5 text-xs ${COLOR_BG[color] ?? COLOR_BG.zinc}`}>
      {children}
    </span>
  );
}

function Filter({
  label,
  desc,
  opts,
}: {
  label: string;
  desc: string;
  opts?: [string, string][];
}) {
  return (
    <div className="rounded border border-zinc-200 p-2 dark:border-zinc-800">
      <p className="text-xs font-semibold text-zinc-900 dark:text-zinc-50">{label}</p>
      <p className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">{desc}</p>
      {opts && (
        <ul className="mt-1.5 space-y-0.5 text-[11px]">
          {opts.map(([k, v]) => (
            <li key={k}>
              <code className="rounded bg-zinc-100 px-1 dark:bg-zinc-800">{k}</code>
              <span className="ml-1 text-zinc-600 dark:text-zinc-400">{v}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Col({ label, desc }: { label: string; desc: string }) {
  return (
    <div className="flex items-start gap-2">
      <code className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-bold text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
        {label}
      </code>
      <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{desc}</span>
    </div>
  );
}

function Sig({ short, ko, detail }: { short: string; ko: string; detail: string }) {
  return (
    <div className="rounded border border-zinc-200 p-2 dark:border-zinc-800">
      <div className="flex items-baseline gap-2">
        <code className="rounded bg-zinc-900 px-1.5 py-0.5 text-[11px] font-bold text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900">
          {short}
        </code>
        <span className="font-semibold text-zinc-900 dark:text-zinc-50">{ko}</span>
      </div>
      <p className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">{detail}</p>
    </div>
  );
}

function Row3({
  a,
  b,
  c,
  badge,
}: {
  a: string;
  b: string;
  c: string;
  badge: string;
}) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5">
        <span className={`rounded px-1.5 py-0.5 text-xs font-bold ${COLOR_BG[badge] ?? COLOR_BG.zinc}`}>
          {a}
        </span>
      </td>
      <td className="px-2 py-1.5 font-medium">{b}</td>
      <td className="px-2 py-1.5 text-[11px] text-zinc-600 dark:text-zinc-400">{c}</td>
    </tr>
  );
}

function Row2({ a, b }: { a: string; b: string }) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5 text-zinc-600 dark:text-zinc-400">{a}</td>
      <td className="px-2 py-1.5 text-right font-mono">{b}</td>
    </tr>
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
