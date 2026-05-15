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
      title="📖 History 도움말"
      subtitle="과거 매매 plan + 5d outcome 누적 PnL 추이"
    >
      <HelpSection emoji="🎯" title="이 페이지의 목적">
        <p>
          과거에 입력한 매매 plan(`/trading`에서 저장한 것)과 그 5일 보유
          outcome을 시계열로 표시. 어느 종목에서 얼마 벌고 잃었는지 누적 추적.
        </p>
      </HelpSection>

      <HelpSection emoji="📈" title="PnL Sparkline">
        <p>
          상단 곡선: 날짜별 누적 paper PnL (5d outcome 기준).
        </p>
        <HelpBullets>
          <li>
            <strong>녹색</strong> — 누적 양수
          </li>
          <li>
            <strong>빨강</strong> — 누적 음수
          </li>
          <li>
            <strong>점선</strong> — 0 기준선
          </li>
        </HelpBullets>
        <HelpNote>
          5d outcome이 backfill 안 된 plan은 곡선에 안 나옴 — 5일 기다려야 함.
        </HelpNote>
      </HelpSection>

      <HelpSection emoji="📋" title="기간 선택">
        <p>최근 7/30/90/180일 toggle. 짧으면 noise, 길면 trend 명확.</p>
      </HelpSection>

      <HelpSection emoji="📊" title="Plan 표 (하단)">
        <HelpBullets>
          <li>
            <strong>plan_date</strong> — 입력한 날
          </li>
          <li>
            <strong>symbol</strong> — 종목 코드
          </li>
          <li>
            <strong>시스템</strong> — system_source 배지로 plan 출처 표시:
            <span className="mx-1 rounded bg-purple-100 px-1 py-0.5 text-[10px] dark:bg-purple-950 dark:text-purple-300">v10</span>
            (스윙 통합 기본),
            <span className="mx-1 rounded bg-amber-100 px-1 py-0.5 text-[10px] dark:bg-amber-950 dark:text-amber-300">v9 fallback</span>
            (스윙 v10 부족 시 보충),
            <span className="mx-1 rounded bg-blue-100 px-1 py-0.5 text-[10px] dark:bg-blue-950 dark:text-blue-300">intraday_v1</span>
            (단타).
          </li>
          <li>
            <strong>amount / shares</strong> — 입력값
          </li>
          <li>
            <strong>1d/5d/10d 수익률</strong> — backfill된 horizon별 결과
          </li>
          <li>
            <strong>실손익 ($)</strong> — shares × (exit − entry)
          </li>
        </HelpBullets>
        <HelpNote>
          단타·스윙 plan을 system_source 배지로 한눈에 구분할 수 있습니다. 시스템별
          누적 PnL을 직접 비교하고 싶으면 시스템 비교(/comparison) 페이지의 5-way
          카드를 참고하세요.
        </HelpNote>
      </HelpSection>

      <HelpSection emoji="❓" title="자주 묻는 질문">
        <HelpFaq q="실제 매매 손익과 다른가요?">
          네. 이건 yfinance 일봉 시초가→종가 paper PnL. 실 체결가는 슬리피지/세금
          반영 안 됨. Alpaca paper 계좌(또는 Webull 앱)에서 실체결 손익 확인 필요.
        </HelpFaq>
        <HelpFaq q="포지션이 곡선에 안 보여요">
          5d backfill이 아직 안 끝남. 16:35 ET cron이 매일 backfill — 영업일 5일
          경과 후 표시.
        </HelpFaq>
      </HelpSection>
    </HelpShell>
  );
}
