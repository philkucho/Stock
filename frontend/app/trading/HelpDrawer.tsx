"use client";

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
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
          <div>
            <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-50">
              📖 매매 Plan 도움말
            </h2>
            <p className="text-[11px] text-zinc-500">단타 + 스윙 추천 + 입력 (Alpaca paper 자동매매 활성)</p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
            aria-label="닫기"
            title="닫기 (ESC)"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 8.586l4.95-4.95 1.414 1.414L11.414 10l4.95 4.95-1.414 1.414L10 11.414l-4.95 4.95-1.414-1.414L8.586 10 3.636 5.05 5.05 3.636 10 8.586z" />
            </svg>
          </button>
        </div>

        <div className="h-[calc(100vh-4.5rem)] overflow-y-auto p-5 text-sm text-zinc-700 dark:text-zinc-300">
          <Section emoji="🎯" title="이 페이지는 무엇인가">
            <p>
              매일 09:25 ET 이후, <strong>단타·스윙 두 트랙</strong>의 추천 종목에 대해
              사용자가 종목별 달러 금액을 입력하는 페이지입니다.
            </p>
            <Bullets>
              <li><strong>⚡ 단타 (Intraday)</strong> — ORB/VWAP/상대거래량 기반, confirm 통과 시 자동 발송</li>
              <li><strong>📈 스윙 (Swing)</strong> — 통합 v10 압축·팽창 셋업, 다일 보유</li>
            </Bullets>
            <p className="mt-2">
              저장된 plan은 DB에 보관되고, 매일 16:35 ET 자동으로 1일/5일/10일 후
              실현 수익을 계산해서 추적합니다.
            </p>
            <Note>
              <strong>Alpaca paper 자동매매 활성</strong> (2026-05-08~). 저장한 plan은
              09:25 ET 직후 cron이 bracket order로 발송. 실거래로 옮기려면 별도 승인 필요.
            </Note>
          </Section>

          <Section emoji="🟢" title="시장 분위기 (Market Brief)">
            <p>페이지 상단의 분위기 카드:</p>
            <Bullets>
              <li><strong>🟢 공격모드</strong> (12-15) — 풀 사이즈 OK</li>
              <li><strong>🟡 중립</strong> (7-11) — 포지션 사이즈 ×0.7 권장</li>
              <li><strong>🛑 방어모드</strong> (0-6) — long 진입 차단, picks 0</li>
            </Bullets>
            <p className="mt-2">7개 신호 (SPY/QQQ/IWM/VIX/A-D Line/FTD)로 판정.</p>
          </Section>

          <Section emoji="📋" title="추천 카드 (단타·스윙 공통)">
            <p>각 카드의 공통 정보:</p>
            <Bullets>
              <li><strong>Composite</strong> — 시스템 통합 점수 (스윙 0~115, 단타 0~110)</li>
              <li><strong>진입가 / 손절가</strong> — 매수·손절 기준</li>
              <li><strong>1R/2R 목표</strong> — 익절 기준 (1R = 진입가 − 손절가)</li>
              <li>system_source 배지: <strong>v10</strong>(스윙 기본) · <strong>v9 보충</strong>(v10 부족 시) · <strong>intraday_v1</strong>(단타)</li>
            </Bullets>
            <p className="mt-3 font-semibold">📈 스윙 전용:</p>
            <Bullets>
              <li><strong>Tier 1</strong> v3 priority 통과 / <strong>Tier 2</strong> scanner 엄격 통과</li>
              <li>🌟 Golden Setup, 📈 PEAD, Stage 2 등 셋업 배지</li>
              <li>score_breakdown(가산 항목 점수 분해) 표시</li>
            </Bullets>
            <p className="mt-3 font-semibold">⚡ 단타 전용:</p>
            <Bullets>
              <li><strong>ConfirmStatusBadge</strong> — ⏳ watchlist / ✅ passed / ❌ failed / 📤 sent</li>
              <li>premarket gap %, premarket RVOL, ORB high/low, session VWAP, intraday RVOL</li>
              <li>provisional entry/stop은 ORB confirm 시 실제 levels로 덮어씀</li>
            </Bullets>
          </Section>

          <Section emoji="💵" title="투자 금액 입력">
            <p>각 카드 하단에 달러 금액 입력란.</p>
            <p className="mt-2">입력 시 자동 계산:</p>
            <Bullets>
              <li><strong>주식수</strong> = floor(금액 / 진입가)</li>
              <li><strong>손실 한도</strong> = 주식수 × (진입가 − 손절가)</li>
              <li><strong>1R/2R 도달 시 수익</strong> 미리보기</li>
            </Bullets>
            <p className="mt-2">[저장] 누르면 DB에 저장. 같은 종목 다시 저장하면 갱신 (upsert).</p>
            <Note>
              <strong>금액이 진입가보다 작으면 0주</strong> — 더 큰 금액 입력 필요.
              <br/>예: LRCX 진입가 $300 → 최소 $300 이상.
            </Note>
          </Section>

          <Section emoji="📊" title="합계 카드 (하단 sticky)">
            <Bullets>
              <li><strong>총 노출</strong> — 모든 종목 입력 금액 합산</li>
              <li><strong>총 위험</strong> — 모든 손실 한도 합산</li>
            </Bullets>
            <p className="mt-2 text-[11px]">
              일반 권장: 총 노출 ≤ 자본의 30%, 총 위험 ≤ 자본의 1.5%.
            </p>
          </Section>

          <Section emoji="🕐" title="매일 운영 흐름 (ET)">
            <table className="w-full text-xs">
              <tbody>
                <SimpleRow left="09:00" desc="프리마켓 데이터 90% 누적" />
                <SimpleRow left="09:15" desc="📧 데일리 이메일 발송 (대시보드 + 통합 Top)" />
                <SimpleRow left="09:25" desc="단타·스윙 picks 산출 → 이 페이지에서 금액 입력 + 저장 → bracket order 발송 (Alpaca paper)" />
                <SimpleRow left="09:30" desc="📈 정규장 개장" />
                <SimpleRow left="09:45" desc="⚡ Intraday ORB confirm — 통과 종목만 실제 levels 확정" />
                <SimpleRow left="16:00" desc="정규장 마감" />
                <SimpleRow left="16:35" desc="⚙ outcome backfill (1d 결과 자동)" />
                <SimpleRow left="다음날" desc="이메일에 어제 plan 결과 표시" />
                <SimpleRow left="+5일" desc="5일 보유 결과 자동 채움" />
                <SimpleRow left="+10일" desc="10일 보유 결과 자동 채움" />
              </tbody>
            </table>
          </Section>

          <Section emoji="📈" title="수익 추적">
            <p>저장된 plan에 대해 시스템이 자동 계산:</p>
            <Bullets>
              <li><strong>1d 수익률</strong> — 다음날 종가 기준 (시초가→종가)</li>
              <li><strong>5d 수익률</strong> — 5거래일 후</li>
              <li><strong>10d 수익률</strong> — 10거래일 후</li>
              <li><strong>SPY 알파</strong> — SPY 같은 기간 수익률 차감</li>
              <li><strong>실손익 ($)</strong> — 주식수 × (exit − entry)</li>
              <li><strong>Target/Stop 도달 여부</strong> — 보유 윈도우 내 high/low 기준</li>
            </Bullets>
          </Section>

          <Section emoji="❓" title="자주 묻는 질문">
            <Faq q="추천이 매일 다르나요?">
              네. 단타·스윙 각각 매일 09:25 ET cron으로 picks 산출.
              regime/시장 상황 + 새 카탈리스트 따라 변동. 단타는 ORB confirm 게이트 추가.
            </Faq>
            <Faq q="단타 섹션이 비어 있어요">
              단타는 regime/gap/RVOL/spread 게이트가 까다로워서 흔히 비어 있음.
              regime defensive면 long 차단, gap &gt; +10% 또는 spread &gt; 1.5%면 자동 skip.
            </Faq>
            <Faq q="규모(달러 금액)는 어떻게 정해야 하나요?">
              일반 권장: 자본의 5-10%/종목, 총 ≤ 30%. Composite 점수 높을수록 비중 ↑ (운영 자유).
              Defensive regime이면 size ×0.4 권장.
            </Faq>
            <Faq q="실제 매매는 누가 하나요?">
              <strong>Alpaca paper 계좌(2026-05-08~)</strong>에는 자동 발송됨 (저장한 plan이 09:25 cron으로
              bracket order). live 계좌는 별도 승인 전까지 비활성. 시스템은 추천 + plan 저장 +
              주문 발송 + 추적까지.
            </Faq>
            <Faq q="저장 후 변경하려면?">
              같은 종목에 다시 [저장] 누르면 갱신. 또는 [취소] 버튼으로 삭제.
            </Faq>
            <Faq q="이메일에 결과가 왜 늦게 나오나요?">
              실시간이 아니라 16:35 ET cron이 yfinance 일봉으로 backfill.
              그 다음날 이메일부터 1d 결과 표시.
            </Faq>
          </Section>
        </div>
      </aside>
    </>
  );
}

function Section({
  emoji,
  title,
  children,
}: {
  emoji: string;
  title: string;
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

function SimpleRow({ left, desc }: { left: string; desc: string }) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5 font-mono text-[11px] text-zinc-700 dark:text-zinc-300">{left}</td>
      <td className="px-2 py-1.5 text-[11px] text-zinc-600 dark:text-zinc-400">{desc}</td>
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
