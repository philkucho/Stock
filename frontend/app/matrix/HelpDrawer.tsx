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
      title="📖 Matrix 도움말"
      subtitle="(종목 × 프리셋) 적합도 매트릭스 — Train/Test 교차 검증"
    >
      <HelpSection emoji="🎯" title="이 페이지의 목적">
        <p>
          모든 종목 × 프리셋 조합의 백테스트 fitness를 한눈에 비교. 색상 진할수록
          fitness ↑.
        </p>
        <p className="mt-2">
          행=종목, 열=프리셋. 클릭하면 상세 셀 정보 + Enable 버튼.
        </p>
      </HelpSection>

      <HelpSection emoji="🎨" title="색상 매핑">
        <HelpBullets>
          <li>
            <strong>녹색 진함</strong> — fitness 1.0+ (강한 적합)
          </li>
          <li>
            <strong>녹색 옅음</strong> — fitness 0.3~1.0 (양호)
          </li>
          <li>
            <strong>회색</strong> — fitness 0~0.3 (약함)
          </li>
          <li>
            <strong>빨강</strong> — fitness 음수 (부적합 — 손실)
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="🔵" title="Trust 모드 (교차검증)">
        <p>
          상단 Trust 토글 ON + train period 선택 시 셀에 dot 표시:
        </p>
        <HelpBullets>
          <li>
            <strong>🔵 ROBUST</strong> — train·test 둘 다 양호 → 신뢰 가능
          </li>
          <li>
            <strong>🔴 OVERFIT</strong> — test만 좋음 → 우연일 수 있음, 위험
          </li>
          <li>
            <strong>⚫ DECAYED</strong> — train만 좋음 → 시장 변화로 망가짐
          </li>
          <li>
            <strong>⚪ WEAK</strong> — 둘 다 약함
          </li>
        </HelpBullets>
        <HelpNote>
          라이브 매매 활성화는 <strong>ROBUST</strong>만 권장.
        </HelpNote>
      </HelpSection>

      <HelpSection emoji="⏱" title="Multi-horizon">
        <p>
          상단 Multi-horizon 토글로 12M / 3M / 1M 매트릭스를 동시에 표시.
          단기·장기 모두 잘 동작하는 셀 식별용.
        </p>
      </HelpSection>

      <HelpSection emoji="🧭" title="Regime 배너">
        <p>
          상단에 현재 시장 regime + 알림. trend / chop / bear 따라 추천 프리셋
          달라짐.
        </p>
      </HelpSection>

      <HelpSection emoji="🔄" title="버튼 동작">
        <HelpBullets>
          <li>
            <strong>Run Matrix</strong> — 새 백테스트 일괄 실행 (시간 오래 걸림)
          </li>
          <li>
            <strong>셀 클릭</strong> → 우측 패널에 상세 + [Enable] 버튼 → /strategies로 활성화
          </li>
        </HelpBullets>
      </HelpSection>

      <HelpSection emoji="❓" title="자주 묻는 질문">
        <HelpFaq q="fitness가 뭐예요?">
          백테스트 종합 점수. Sharpe 비슷한 risk-adjusted. 0.3+ 양호, 1.0+ 강함.
        </HelpFaq>
        <HelpFaq q="OVERFIT인데 fitness 1.5인 셀, 매매해도 되나요?">
          위험. test 기간만 좋고 train 기간 약함 → 우연/lookahead bias 가능. 다른
          기간으로 추가 검증 권장.
        </HelpFaq>
      </HelpSection>
    </HelpShell>
  );
}
