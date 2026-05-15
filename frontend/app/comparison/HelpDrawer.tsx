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
      title="📖 시스템 비교 도움말"
      subtitle="5개 시스템의 picks 실현 수익을 동일 조건으로 비교"
    >
      <HelpSection emoji="🎯" title="이 페이지의 목적">
        <p>
          매일 09:25 ET, 4개 종목선정 시스템이 각자 picks 산출 → DB에 로그 →
          1d/5d/10d 후 실제 OHLC로 수익률 backfill → 이 화면에서 비교.
          통합 시스템은 내부적으로 v10(기본)과 v9(fallback)로 분리해 5-way 카드로 표시.
        </p>
        <p className="mt-2">
          어느 시스템이 진짜 알파를 만드는지 객관적으로 검증.
        </p>
      </HelpSection>

      <HelpSection emoji="🔵" title="5개 시스템 (effective)">
        <HelpBullets>
          <li>
            <strong>v3</strong> — Daily Picks (5-Block 100점, hard gate)
          </li>
          <li>
            <strong>scan_momentum</strong> — 거래량+모멘텀 스캐너 (단순 점수)
          </li>
          <li>
            <strong>통합 v10 (기본)</strong> — v3 priority + 스캐너 보너스 +
            confluence super-multiplier + auto-blacklist
          </li>
          <li>
            <strong>통합 v9 (fallback)</strong> — v10이 quality gate로 부족할 때
            보충된 종목만 따로 추적 (auto-blacklist 미적용)
          </li>
          <li>
            <strong>대시보드 (Tier)</strong> — 자체 평가 파이프라인 (ATR levels
            + Tier S/A/B/C 분류, 시그널 6종)
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="📊" title="KPI 카드 (시스템별)">
        <HelpBullets>
          <li>
            <strong>총 PnL ($)</strong> — 시스템 $10,000 균등 5분배 시뮬
            (top 5 picks × $2,000/주)
          </li>
          <li>
            <strong>평균 수익</strong> — 모든 picks 평균 수익률
          </li>
          <li>
            <strong>SPY 알파</strong> — 같은 기간 SPY 수익률 차감
          </li>
          <li>
            <strong>승률</strong> — 양수 수익률 비율
          </li>
          <li>
            <strong>Sharpe</strong> — risk-adjusted return (1.0+ 양호)
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="📈" title="누적 PnL 곡선">
        <p>
          5일 보유 기준, 시간별 누적 PnL 추이. 시스템별 색상으로 구분.
        </p>
        <HelpNote>
          곡선이 우상향 = 시스템이 SPY보다 알파 만들고 있음. 우하향 = paper 손실.
        </HelpNote>
      </HelpSection>

      <HelpSection emoji="🔄" title="버튼 동작">
        <HelpBullets>
          <li>
            <strong>↻ 오늘 picks 기록</strong> — 09:25 ET cron 수동 실행 (4개
            adapter가 system_pick_logs에 upsert; integrated 행은 score_meta.source로
            v10/v9_fallback 자동 태깅)
          </li>
          <li>
            <strong>🔄 결과 백필</strong> — 16:35 ET cron 수동 실행 (1d/5d/10d
            outcome backfill)
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="❓" title="자주 묻는 질문">
        <HelpFaq q="왜 시스템마다 picks 수가 다르나요?">
          v3는 hard gate로 0~5개, scan_momentum은 항상 top 10, integrated v10은
          quality gate로 0~5개, v9_fallback은 v10이 부족할 때만 출현, 대시보드는
          Tier S/A/B/C 분류 top 5. 시장 약세일수록 통과 적어짐.
        </HelpFaq>
        <HelpFaq q="통합 v10와 v9 fallback 차이는?">
          v10은 auto-blacklist + drawdown gate가 적용된 정식 버전. v10이 종목을
          0~2개만 통과시키는 날엔 v9(이전 세대, 게이트 완화)에서 빈자리를 보충해
          Top {"{N}"}을 매일 채움. score_meta.source 태그가 'v10'/'v9_fallback'으로
          저장되어 통계가 자동 분리됨.
        </HelpFaq>
        <HelpFaq q="대시보드 시스템이 추천한 종목이 매매 Plan에 안 보이는데?">
          별개 평가 파이프라인이라 사용하는 시그널이 다름. 매매 Plan(/trading)은
          통합 v10/v9 + 단타 intraday_v1만 사용. 대시보드 system_id는 비교 페이지
          전용(시스템 성과 측정).
        </HelpFaq>
        <HelpFaq q="실제 매매 수익과 다를 수 있나요?">
          네. 이 비교는 시초가→종가 paper PnL. 실제 체결가는 슬리피지/spread/세금
          등으로 다름. 알파 비교용.
        </HelpFaq>
        <HelpFaq q="기간을 늘리면?">
          최근 30일이 default. 60/90일 늘리면 trend 명확하지만 regime 변화에
          영향받음.
        </HelpFaq>
      </HelpSection>
    </HelpShell>
  );
}
