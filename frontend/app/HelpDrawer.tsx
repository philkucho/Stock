"use client";

import HelpShell, {
  HelpBullets,
  HelpFaq,
  HelpNote,
  HelpSection,
} from "@/components/HelpShell";

export default function HelpDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  return (
    <HelpShell
      open={open}
      onClose={onClose}
      title="📖 Stock Autotrader 안내"
      subtitle="시스템 전체 구조 + 매일 운영 흐름"
    >
      <HelpSection emoji="🎯" title="이 시스템은 무엇인가">
        <p>
          NautilusTrader 기반 미국 주식 자동매매 시스템 (브로커: Alpaca paper · Webull · Fidelity).
          매일 단타·스윙 두 트랙 종목 추천 + 시장 분석 + 자동 주문(paper) + 실현 수익 추적.
        </p>
        <HelpNote>
          <strong>Alpaca paper 자동매매 활성</strong> (2026-05-08~) — 매매 Plan에 저장한
          종목이 09:25 ET cron으로 bracket order 발송. live 계좌는 별도 승인 전까지 비활성.
        </HelpNote>
      </HelpSection>

      <HelpSection emoji="🗺" title="주요 페이지 안내">
        <HelpBullets>
          <li>
            <strong>🎯 대시보드</strong> — Tier S/A/B/C 후보 + 전체 운용 정보
          </li>
          <li>
            <strong>📊 스캐너</strong> — 거래량+모멘텀 점수 상위 종목
          </li>
          <li>
            <strong>Picks</strong> — v3 Daily Picks (5-Block 점수 + hard gate)
          </li>
          <li>
            <strong>🎯 매매 Plan</strong> — 단타·스윙 두 섹션 Top 3 추천 +
            종목별 금액/수량 입력 → Alpaca paper bracket order 자동 발송
          </li>
          <li>
            <strong>📈 시스템 비교</strong> — 5 시스템(v3 / scan_momentum / 통합
            v10 / 통합 v9 fallback / 대시보드) picks의 1d/5d/10d 실현 수익 비교
          </li>
          <li>
            <strong>📒 History</strong> — 과거 plan + PnL 누적 곡선
          </li>
          <li>
            <strong>Strategies / Matrix / Backtests</strong> — 백테스트 검증
            (장기 매매 시스템 튜닝)
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="🕐" title="매일 운영 흐름 (US/Eastern)">
        <table className="w-full text-xs">
          <tbody>
            <SimpleRow time="08:55" desc="Stage 2 daily picks 산출 (cron)" />
            <SimpleRow time="09:00" desc="프리마켓 데이터 90% 누적" />
            <SimpleRow
              time="09:15"
              desc="📧 데일리 이메일 (regime + 대시보드 + 통합 picks + 어제 결과)"
            />
            <SimpleRow
              time="09:25"
              desc="4 시스템 picks 로깅 + 단타·스윙 산출 → /trading 입력 → bracket order 발송 (Alpaca paper)"
            />
            <SimpleRow time="09:30" desc="📈 정규장 개장" />
            <SimpleRow time="09:45" desc="⚡ Intraday ORB confirm 게이트" />
            <SimpleRow time="16:00" desc="정규장 마감" />
            <SimpleRow
              time="16:35"
              desc="⚙ 1d/5d/10d outcome backfill (자동)"
            />
          </tbody>
        </table>
      </HelpSection>

      <HelpSection emoji="🧠" title="종목선정 시스템">
        <p className="text-[11px]">
          시스템 비교(/comparison) 페이지에서 5개 effective system을 나란히 추적:
        </p>
        <HelpBullets>
          <li>
            <strong>v3 Daily Picks</strong> — 5-Block 100점 (Regime / Trend-RS /
            Catalyst-Volume / Setup / Risk) + Hard Gate
          </li>
          <li>
            <strong>scan_momentum</strong> — 거래량 + 모멘텀 단순 점수 (검증
            OOS Sharpe 3.01)
          </li>
          <li>
            <strong>통합 v10 (기본)</strong> — v3 priority + 스캐너 보너스 +
            Confluence super-multiplier + Auto-blacklist + Drawdown-aware
          </li>
          <li>
            <strong>통합 v9 (fallback)</strong> — v10이 부족할 때 보충 (auto-blacklist
            미적용); score_meta.source로 분리 추적
          </li>
          <li>
            <strong>대시보드 (Tier)</strong> — 자체 평가 파이프라인 (ATR levels +
            S/A/B/C Tier 분류)
          </li>
        </HelpBullets>
        <p className="mt-2 text-[11px]">
          매매 Plan(/trading)에는 <strong>통합 v10/v9 fallback</strong>(스윙) +
          <strong> intraday_v1</strong>(단타) 두 트랙만 노출.
        </p>
      </HelpSection>

      <HelpSection emoji="📡" title="현재 화면 (홈)">
        <HelpBullets>
          <li>
            <strong>Backend health</strong> — FastAPI 서버 상태 + 버전
          </li>
          <li>
            <strong>Account</strong> — Webull 계좌 잔액 + buying power (paper
            단계)
          </li>
          <li>
            <strong>상단 nav</strong> — 모든 페이지로 바로 이동
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="❓" title="자주 묻는 질문">
        <HelpFaq q="자동매매가 가능한가요?">
          <strong>Alpaca paper 자동매매 가동 중</strong> (2026-05-08~). 매매 Plan에
          저장한 plan은 09:25 cron이 bracket order로 발송. live 계좌는 별도 승인
          전까지 비활성, 사용자 결정으로 활성화 예정.
        </HelpFaq>
        <HelpFaq q="어디서 시작하면 되나요?">
          매일 09:25 ET 이후 <strong>🎯 매매 Plan</strong> 페이지 → 단타·스윙 두
          섹션 추천 확인 → 금액/수량 입력 → 저장 (저장 시 paper 자동주문). 다음날
          이메일에서 어제 결과 확인.
        </HelpFaq>
        <HelpFaq q="API 서버가 안 켜져 있을 때?">
          <code>uvicorn api.main:app --reload --port 8000</code> 실행. 그 후
          페이지 새로고침.
        </HelpFaq>
        <HelpFaq q="이메일을 못 받으면?">
          09:15 ET cron이 보냅니다. <code>scripts/daily_email_report.py</code>{" "}
          수동 실행하거나, .env의 EMAIL 설정 확인.
        </HelpFaq>
      </HelpSection>
    </HelpShell>
  );
}

function SimpleRow({ time, desc }: { time: string; desc: string }) {
  return (
    <tr className="border-t border-zinc-200 dark:border-zinc-800">
      <td className="px-2 py-1.5 font-mono text-[11px] text-zinc-700 dark:text-zinc-300">
        {time}
      </td>
      <td className="px-2 py-1.5 text-[11px] text-zinc-600 dark:text-zinc-400">
        {desc}
      </td>
    </tr>
  );
}
