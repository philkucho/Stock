# 자동매매 시스템 — 리스크/운영 보완 로드맵

**작성일**: 2026-05-08
**근거**: 투자 전문가(Plan agent) 검토 — `risk_ops_review_2026-05-08`
**핵심 메시지**: alpha 발굴은 hobby-grade 이상의 rigor 갖춤. 그러나 risk·ops가 1-2세대 뒤처짐. **alpha 더 chase하기 전 risk·ops를 alpha 수준으로 끌어올리는 게 자본 보존에 10배 critical**.

---

## 평가 점수 요약 (10점 만점)

| 차원 | 점수 | 핵심 평가 |
|---|---|---|
| 알파 강건성 | 5/10 | 60일 강세장 단일 sample, Sharpe 8.81은 의심. overfit 신호 다수 |
| **리스크 관리** | **4/10** | daily loss limit / correlation cap / gap-down 보호 등 표준 항목 다수 누락 |
| 실행 효율성 | 6/10 | 2-tier 50:50은 합리적이나 trailing stop / time-based exit 부재 |
| **운영 안정성** | **3/10** | PC 단일 실패점, alerting 부재, broker drift 감지 X |
| 규제·세금·심리 | 5/10 | wash sale 부분 회피 우연, live 전환 전 세무 검토 필요 |

---

## Top 5 즉시 보완 (1주 이내)

### ① Daily Loss Limit + Portfolio Heat Circuit Breaker ⚠️ 가장 시급
- **상태**: ☑ **완료 (2026-05-08)**
- **문제**: 매일 5종목 × $20K × -1.5% 동시 stop hit 시 연속 5일 → -7.5%~-37.5% 자본 노출 가능
- **해결**:
  - `run_trade` 진입 게이트에 `_check_daily_loss_limit()` 추가
  - `account.last_equity` vs `account.equity`로 daily_pnl_pct 계산
  - **halt_pct=-3%** 도달 시 신규 진입 차단 (기존 OCO 유지)
  - **close_pct=-5%** 도달 시 alert (옵션 `DAILY_LOSS_AUTO_CLOSE=true` 시 강제 close_all)
- **코드 위치**: `scripts/daily_pipeline.py:run_trade()` + `broker_adapter/base.py:AccountSummary.last_equity` 추가
- **단일 가장 큰 파산 보호**

### ② Sector Concentration Cap at Trade Phase
- **상태**: ☑ **완료 (2026-05-08)** — `SECTOR_CAP=2` (기본), `run_trade` 루프에 sector_count Counter
- **문제**: `compress_by_sector` (stage2_daily_picks.py:868)는 picks 단계만 적용. 사용자가 `/trading`에 5개 다 반도체 입력 가능 → 무방비
- **해결**: `run_trade` 루프에서 `sector_count` 유지, 동일 sector ≥ 2 reject. `skipped` reason="sector_concentrated"
- **코드 위치**: `scripts/daily_pipeline.py:run_trade()` 발송 루프 내

### ③ OOS 약세장 검증 (2022 walk-forward) — alpha 진성 검증
- **상태**: ☑ **완료 (2026-05-08)** — `scripts/v10_oos_bear_2022.py`
- **결과**: 2022 H1 6개 sample dates 모두 **picks=0** (regime 10/neutral인데도)
  - Scanner: 21개 momentum picks 생성 (예: BSX, J, CMG, DOW)
  - v3 historical: 0개 (strict setup score 모두 fail)
  - Integrated v10: 0개 (compression+expansion 요구로 모두 reject)
- **결론**:
  - ✅ **자본 보존 PASS** — v10이 약세장 자동 차단 → trade 발생 X → 손실 0
  - ❌ **Alpha 검증 INCONCLUSIVE** — picks 0이라 outcomes 측정 불가
  - ⚠️ **시스템 본질**: "강세장 limited alpha + 약세장 auto-stop". compression+expansion + Stage 2 setup 의존이 강세장 종목 패턴에 묶여 자연 보호
- **시사점**:
  - v10 retire 불필요 (가짜 alpha 발사 안 함 확인)
  - 약세장 alpha 노리려면 별도 strategy (mean-reversion, defensive sector) 필요 — 비범위
  - Regime engine 일일 변동률 보강 가능 (선택)
  - scan_momentum 단독 시스템 OOS는 별도 가치 있는 실험

### ④ Heartbeat Alerting + Reconciliation
- **상태**: ☑ **완료 (2026-05-08)** — `notifications/heartbeat.py`, `HEARTBEAT_ENABLED` env. `daily_pipeline` 각 phase 시작/완료/실패 ping + backfill 후 broker drift 감지
- **문제**: PC 슬립/리부트/Windows update 시 09:25 task miss → silent failure
- **해결**:
  - `daily_pipeline` 각 phase 시작/종료에 이메일 ping
  - 30분 무응답 시 SMS (Twilio 또는 ntfy.sh)
  - 종가 후 reconciliation: Alpaca `get_positions()` vs DB `trade_plans` 불일치 alert
- **코드 위치**: `scripts/daily_pipeline.py` + 신규 `notifications/heartbeat.py`

### ⑤ Breakeven Trailing Stop After 1차 Hit
- **상태**: ☑ **완료 (2026-05-09)** — `daily_pipeline --phase monitor`. 가격 기반 1차 hit 감지(yfinance 1m), `replace_order_stop`로 잔여 50% stop을 entry로 갱신. score_meta에 `stop_raised_to_breakeven` 멱등 flag
- **문제**: 1차 hit 후 잔여 50%가 가격 회귀 시 -1R stop으로 모두 손실 → 2-tier 의의 약화
- **해결**:
  - 1차 fill webhook 또는 polling → 잔여 50% stop을 entry price로 raise (alpaca `replace_order`)
  - 또는 `--phase monitor` 추가 (5분 주기, 1차 fill 감지 시 stop 갱신)
- **코드 위치**: `broker_adapter/alpaca_adapter.py` + `scripts/daily_pipeline.py`

---

## 1주 (자본 보호 후)

- [ ] **Backfill outcomes에 daily loss 영향 시뮬** — `outcomes.py`에 daily_loss_limit 적용 시뮬레이션 추가
- [ ] **Alert 채널 검증** — 이메일 + SMS 둘 다 실패 시 fallback (Discord webhook 등)

## 1개월 (alpha 검증 후)

- [ ] **Correlation cap** — open positions + 신규 plan의 30일 ρ_avg > 0.6 reject
- [ ] **Time-based exit** — 5일/10일 보유 만기 자동 청산 (`monitor` phase)
- [ ] **2-tier ratio sweep simulation** — `scripts/simulate_stops.py` 활용해 [30:70, 40:60, 50:50, 60:40, 70:30] × [breakeven_trail Y/N] × [time_exit 5d/10d] grid. Sharpe + max DD 매트릭스 출력
- [ ] **Gap-down slippage 보호** — earnings 1일 전 종목 자동 size halve 또는 exclude

## 장기 (Webull live 전환 전)

- [ ] **Regime-adaptive ratio** — aggressive 30:70 / neutral 50:50 / defensive long_blocked
- [ ] **Cloud cron 이중화** — AWS Lambda or Render free tier (PC 단일 실패점 해소)
- [ ] **Wash sale rule + trader status election 회계사 상담** — IRC §1091, §475(f)
- [ ] **2008/2020/2022 frozen test set 분리** — 3개 약세장 별도 OOS

---

## 2-tier 비율 데이터 가설 (1개월 항목 ⑩에서 시뮬 후 결정)

| Ratio | 강세장 (현 데이터) | 약세장 (가설) | 평가 |
|---|---|---|---|
| **50:50 (현)** | +2.0R | +0.5R | 균형 |
| 30:70 | **+2.4R** ★ | +0.3R | 강세장 우위 / 약세장 취약 |
| 70:30 | +1.8R | **+0.6R** ★ | 약세장 우위 |
| **50:50 + breakeven trail** | **+2.1R + DD↓** | **+0.6R** | **양 시장 안정** ★★ |

→ **잠정 가설**: 50:50 + breakeven trail이 robust optimal. regime-adaptive ratio도 검토.
→ **검증 방법**: v10 backfill 데이터 + 2022 OOS 데이터로 Sharpe / max DD 매트릭스.

---

## 미해결 의문 / 향후 검토

- **v10 alpha 진성 여부** — Top 5 ③ OOS 결과 대기
- **Webull live 전환 시점** — paper 90일 무중단 SLA + alpha 검증 통과 후
- **Roth IRA day trade 위반 방지** — 코드 레벨 hard block 이미 있음 (user_profile 메모리 참조). live 전환 시 재확인
- **VIX > 25 trade phase 동작** — v10 picks 단계만 강화됨. trade phase에서 추가 regime check 필요한지

---

## 본질적 진단

> 이 시스템은 **alpha discovery 측면에서는 잘 설계된 hobby-grade quant**다 — 5-block scoring, regime engine, 3-system A/B 비교, 2-tier 부분 청산까지 retail 수준에서 보기 드문 architectural rigor. 그러나 **risk management와 operational resilience가 alpha layer 대비 1-2세대 뒤처져 있다** — 60일 강세장 단일 sample로 Sharpe 8.81을 alpha 증거로 채택하고, daily loss limit·correlation cap·heartbeat·OOS 약세장 검증이 모두 부재한 상태로 Webull live 전환을 계획 중이다. **alpha를 더 chase하기 전에 risk·ops를 alpha 수준으로 끌어올리는 것이 자본 보존에 10배 critical**.
