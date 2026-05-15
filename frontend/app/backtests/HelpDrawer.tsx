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
      title="📖 Backtests 도움말"
      subtitle="과거 데이터로 전략 성과 검증한 결과 목록"
    >
      <HelpSection emoji="🎯" title="이 페이지의 목적">
        <p>
          전략(SmaCross, Composite 등)을 과거 데이터로 돌린 백테스트 결과 목록.
          종목/전략 필터로 검색하고, 행 클릭 시 상세 화면으로.
        </p>
        <HelpNote>
          새 백테스트는 CLI로 적재:{" "}
          <code>python -m backtests.run_sma_cross --save</code>
        </HelpNote>
      </HelpSection>

      <HelpSection emoji="📋" title="컬럼 설명">
        <HelpBullets>
          <li>
            <strong>ID</strong> — 클릭 시 상세 화면 (PnL chart 포함)
          </li>
          <li>
            <strong>Strategy</strong> — 사용된 전략 이름 (예: SmaCross)
          </li>
          <li>
            <strong>Symbol / Interval</strong> — 대상 종목 / 봉 단위 (1d, 1h, 5m)
          </li>
          <li>
            <strong>Period</strong> — 백테스트 기간 (시작 ~ 끝)
          </li>
          <li>
            <strong>PnL</strong> — 총 손익 ($) — 양수=수익, 음수=손실
          </li>
          <li>
            <strong>Win%</strong> — 승률 (양수 trade / 총 trade)
          </li>
          <li>
            <strong>Trades</strong> — 체결된 포지션 수
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="🔍" title="필터 사용">
        <p>
          Symbol / Strategy 입력 → Apply. 빈 칸이면 전체. 페이지당 20개씩.
        </p>
      </HelpSection>

      <HelpSection emoji="⚖" title="결과 해석">
        <HelpBullets>
          <li>
            <strong>PnL 양수 + Win% 50%+</strong> — 전략이 그 종목/기간에 잘 맞음
          </li>
          <li>
            <strong>Trades 적음 (&lt;10)</strong> — 통계적 유의성 낮음 — 표본 더
            필요
          </li>
          <li>
            <strong>큰 PnL + 낮은 Win%</strong> — 한두 번 큰 수익으로 평균 끌어올림
            (Composite/Maven 스타일)
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="❓" title="자주 묻는 질문">
        <HelpFaq q="과거 결과 잘 나오면 실거래도 잘 되나요?">
          꼭 그렇진 않음. Overfitting 가능 — Matrix 페이지에서 train/test 두
          기간으로 ROBUST/OVERFIT 분류 확인.
        </HelpFaq>
        <HelpFaq q="새 백테스트 적재하려면?">
          터미널에서{" "}
          <code>python -m backtests.run_sma_cross --symbol AAPL --save</code>{" "}
          식으로. 더 자세한 옵션은 README 참조.
        </HelpFaq>
      </HelpSection>
    </HelpShell>
  );
}
