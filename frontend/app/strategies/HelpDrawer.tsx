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
      title="📖 Strategies & Signals 도움말"
      subtitle="시그널·프리셋·활성 전략 관리"
    >
      <HelpSection emoji="🎯" title="이 페이지 구조 (3 섹션)">
        <HelpBullets>
          <li>
            <strong>활성 전략</strong> — 라이브 매매 단계에서 매수/매도 시그널
            생성하는 (종목 × 프리셋) 조합. enabled만 매매에 사용
          </li>
          <li>
            <strong>거장 프리셋</strong> — Minervini/Cameron/Khan/Zanger 등
            스타일별 시그널 가중합 묶음
          </li>
          <li>
            <strong>Signal Library</strong> — 사용 가능한 모든 단순 룰 시그널 목록
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="⚡" title="Signal Library">
        <p>각 시그널은 하나의 단순 룰. 예:</p>
        <HelpBullets>
          <li>
            <strong>volume</strong> — 거래량 기반 (RVOL 등)
          </li>
          <li>
            <strong>trend</strong> — 추세 추종 (SMA cross, EMA stack)
          </li>
          <li>
            <strong>reversal</strong> — 평균 회귀 (RSI 과매도 등)
          </li>
          <li>
            <strong>breakout</strong> — 돌파 (52w high, range break)
          </li>
          <li>
            <strong>filter</strong> — 진입 차단 (regime, market state)
          </li>
        </HelpBullets>
        <p>
          <strong>Min bars</strong>: 시그널 계산에 필요한 최소 봉 수.
        </p>
      </HelpSection>

      <HelpSection emoji="🎨" title="거장 프리셋 카드">
        <HelpBullets>
          <li>
            <strong>active_signals</strong> — 이 프리셋이 사용하는 시그널 묶음
          </li>
          <li>
            <strong>Buy threshold</strong> — N개 시그널 중 X개 이상 충족 시 매수
          </li>
          <li>
            <strong>Sell threshold</strong> — 음수 (역방향 시그널 임계값)
          </li>
          <li>
            <strong>Stop loss / Take profit</strong> — 자동 매도 임계 (%)
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="🔄" title="활성화 흐름">
        <HelpBullets>
          <li>
            Matrix 페이지에서 (종목 × 프리셋) 적합도 확인
          </li>
          <li>fitness ≥ 0.3 + ROBUST 분류 셀 클릭</li>
          <li>Enable → 이 페이지의 활성 전략 목록에 추가</li>
          <li>★ Enabled 토글 / Delete 버튼으로 관리</li>
        </HelpBullets>
        <HelpNote>
          enabled = false인 row는 매매 시그널 안 만듦. 일시 정지에 사용.
        </HelpNote>
      </HelpSection>

      <HelpSection emoji="❓" title="자주 묻는 질문">
        <HelpFaq q="active_signals 가중치는 어디서?">
          모든 active 시그널이 동등 가중. CompositeStrategy가 단순 합산.
        </HelpFaq>
        <HelpFaq q="새 프리셋 만들 수 있나요?">
          현재는 코드(`api/strategies/presets.py`)에서 정의. UI 추가는 추후.
        </HelpFaq>
      </HelpSection>
    </HelpShell>
  );
}
