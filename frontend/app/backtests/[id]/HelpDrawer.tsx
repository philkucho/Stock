"use client";

import HelpShell, {
  HelpBullets,
  HelpFaq,
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
      title="📖 Backtest 상세 도움말"
      subtitle="개별 백테스트 결과 분석"
    >
      <HelpSection emoji="📊" title="요약 카드 (상단)">
        <HelpBullets>
          <li>
            <strong>Total PnL</strong> — 총 손익 ($)
          </li>
          <li>
            <strong>Return</strong> — 자본 대비 수익률 (%)
          </li>
          <li>
            <strong>Win Rate</strong> — 승률
          </li>
          <li>
            <strong>Trades</strong> — 체결된 포지션 수
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="📈" title="PnL 분포 차트">
        <p>3개 막대:</p>
        <HelpBullets>
          <li>
            <strong>Best</strong> — 최대 수익 trade ($)
          </li>
          <li>
            <strong>Avg</strong> — 평균 trade PnL
          </li>
          <li>
            <strong>Worst</strong> — 최대 손실 trade
          </li>
        </HelpBullets>
        <p className="mt-2">
          Best/|Worst| 비율이 클수록 right-tail (큰 수익) 전략. 작을수록
          mean-reversion 전략.
        </p>
      </HelpSection>

      <HelpSection emoji="⚖" title="평가 기준">
        <HelpBullets>
          <li>
            <strong>Return ≥ 10% / 연환산</strong> — 시장 평균 (SPY ~10%/y) 이상
          </li>
          <li>
            <strong>Win Rate ≥ 50%</strong> — 일반적 좋은 trend 전략
          </li>
          <li>
            <strong>Worst &lt; -20%</strong> — drawdown 큰 전략 — 위험 검토 필요
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="❓" title="자주 묻는 질문">
        <HelpFaq q="이 결과로 라이브 매매해도 되나요?">
          단일 백테스트만으로는 부족. Matrix 페이지에서 다른 기간(train/test)
          교차 검증 후 ROBUST 분류된 조합만 권장.
        </HelpFaq>
        <HelpFaq q="strategy_params는 어디서 보나요?">
          이 페이지 하단 raw JSON 블록에 표시. 사용된 indicator 임계값,
          fast/slow MA 등.
        </HelpFaq>
      </HelpSection>
    </HelpShell>
  );
}
