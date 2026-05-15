# 다른 자동매매 시스템 주문체계 리서치 — 비교표 + 권장사항

## Context

5/8 paper 발송 폭주(57개 canceled bracket — AMZN/SPY/AVGO/NVDA/VRT) 사후조사 중,
우리 시스템의 자동 발송 경계(orb_auto vs user_fixed)와 lifecycle 관리(`confirm_status` + `OrderStatus`)가 외부 표준 대비 어떤 모양인지 가늠하기 위해 조사. **이번 산출물은 코드 변경이 아니라 리서치 보고서** — 후속 개선 결정의 입력자료.

조사 범위:
- 오픈소스 bot: **Freqtrade, Jesse, Hummingbot**
- Institutional algo: **NautilusTrader, QuantConnect Lean, Backtrader**

측면:
1. Intent vs Execution 분리 (계획/승인/발송 계층)
2. 주문 lifecycle (pending → sent → filled → reconcile)

---

## 1. 6개 시스템 비교표

| 항목 | Freqtrade | Jesse | Hummingbot | NautilusTrader | Lean | Backtrader |
|---|---|---|---|---|---|---|
| **Intent/Execution 분리** | 분리(callback gate) | 약하게 | 명확(ConnectorBase) | **가장 엄격(MessageBus)** | 분리(TransactionHandler) | 분리(단순) |
| **명시적 RiskEngine** | callback (`confirm_trade_entry`) | 없음 | strategy 내부 | **`RiskEngine` 컴포넌트** | `BuyingPowerModel`+`RiskManagement` | 없음 (Sizer만) |
| **사전 거부 상태** | callback False → abort | 없음 | budget skip | **`DENIED` 터미널 상태** | **`OrderStatus.Invalid`** | `Order.Margin`/`Rejected` |
| **user vs strategy 구분** | `enter_tag` string | `submitted_via` enum (SL/TP만) | `HBOT-` prefix | `client_order_id` + `StrategyId` 태그 | **`OrderTag` string** | `Order.info` AutoDict |
| **상태 모델** | string-only (`ft_is_open` bool) | string enum 5개 | **IntEnum 11 states** | **enum 14 states** | C# enum 9 states | 정수 상수 9개 |
| **상태 전이** | polling (CCXT) | tick + WS | event + 1s clock tick | **순수 이벤트(MessageBus)** | OrderEvent → callback | next() + notify_order |
| **WS+REST 듀얼** | REST polling only | live driver 의존 | **WS user-stream + REST 백업** | **WS push + 주기 history poll** | brokerage plugin마다 | 약함 |
| **Reconciliation** | `startup_update_open_orders` | docs 빈약 | `client_id`↔`exchange_id` 듀얼 매칭 | **`LiveExecutionEngine` 내장** (Report 3종) | `GetOpenOrders` 시작 시 | IB store 의존 |

**핵심 cross-system 관찰**:
- 6/6 모두 Intent/Execution 분리는 함 (정도 차이만)
- 5/6 enum 기반 상태 모델 (Freqtrade만 예외)
- 4/6 이벤트 push 방식 (Freqtrade/Jesse polling)
- user-vs-strategy 구분은 **string tag 또는 ID prefix** 패턴이 대세 — **우리 `dispatch_mode` enum은 비교 시스템 어디에도 없는 차별점**

---

## 2. 우리 시스템 현재 매핑

### Intent (plan 생성)
- **`orb_auto`** (자동) — `scripts/daily_pipeline.py:343-450` `run_preopen` → 5-Model Stack → Top 5 watchlist 생성. `dispatch_mode='orb_auto'`, `confirm_status='watchlist'`. 보호: `'sent'` 또는 `user_fixed`는 덮어쓰지 않음 (L437-439).
- **`user_fixed`** (수동) — `api/routes/trading.py:626-706` `save_plan`. Frontend `/trading` UI → POST `/api/trading/plan` → DB insert. 같은 날짜/심볼은 항상 user_fixed가 덮어씀.

### Approval/Gating (9중 안전장치)
- `scripts/intraday_confirm.py:111-450` (orb_auto 발송 경로):
  1. `AUTO_TRADE_ENABLED=false` → dry-run (L124-126)
  2. `AUTO_CONFIRM_DISPATCH=false` → 전체 비활성 (L140-150)
  3. Regime 방어모드 → `confirm_status='failed'` (L169-187)
  4. ORB+VWAP+RVOL 4-pass 실패 (L245)
  5. `account.trading_blocked` (L273-276)
  6. Daily loss halt -3% (L279-292)
  7. Daily loss close -5% → 전 cancel (L284-292)
  8. Position cap 5종목 (L304, 346-348)
  9. Sector cap 2종목 (L302, 350-354)
- `scripts/daily_pipeline.py:462-722` `run_trade` (user_fixed 발송 경로): 1~7번 + position/sector cap + Reentry 방지 (L593-602).

### Execution (broker 발송)
- `broker_adapter/alpaca_adapter.py:138-227` `place_bracket_order(req, *, dry_run=False)`. Dry-run 분기 L243. 2-tier 분할 시 1차 cancel rollback (L215-225).
- `broker_order_ids` 저장: `daily_pipeline.py:688-692` (user_fixed), `intraday_confirm.py:426-429` (orb_auto).

### Lifecycle (이중 상태 모델)
- **TradePlan.confirm_status** (plan 수준): `watchlist` → `passed`/`failed` → `sent`/`skipped`. `api/db/models.py:319-386`.
- **OrderStatus** (broker 수준): `PENDING / SUBMITTED / PARTIALLY_FILLED / FILLED / CANCELED / REJECTED / EXPIRED`. `api/db/models.py:38-46`. — **Nautilus 14-state의 부분집합**.
- Monitor (`daily_pipeline.py:132-270`): 1차 target 도달 시 잔여 50% stop을 breakeven으로 갱신. `score_meta['stop_raised_to_breakeven']=True`만 추가.
- Reconciliation: `daily_pipeline.py:107-128` → `notifications/heartbeat.py:reconcile_broker_state()`. Alpaca positions vs DB plans 불일치 검출 → alert.

### 외부 진입점
- Windows Task: `_Preopen` (09:25) / `_Confirm` (09:45, **현재 Disabled**) / `_Trade` (09:30) / `_Monitor` (11:30) / `_BackfillOutcomes` (16:30) / `_LogPicks`.
- FastAPI: `POST /api/trading/plan`, `GET /api/trading/today`.

---

## 3. 권장사항 (적용 가능성 높은 순)

### A. 즉시/낮은 비용 — 운영 정리

**A1. `dispatch_mode` enum 유지 + `client_order_id` prefix로 이중 인코딩**
- 현재 broker_order_ids에 의도/전략 추적 정보 없음. 권장 포맷: `{env}-{dispatch_mode}-{strategy}-{YYYYMMDDHHMMSS}-{seq4}` 예: `paper-orb_auto-v10-20260514093015-0042`.
- 효과: Alpaca history grep만으로도 5/8 같은 폭주 사고의 발신 경로 즉시 식별. Lean/Hummingbot 표준 패턴.
- 영향: `place_bracket_order` 호출부에 `client_order_id` 인자 추가 — 1~2 파일 수정.

**A2. `dispatch_mode` 새 값 `manual_oneshot` 도입 권고 검토**
- 현재 `orb_auto`/`user_fixed` 2종으로는 "테스트 수동 호출"(5/8 같은 케이스)이 user_fixed로 섞임. `manual_oneshot` 또는 `test`를 별개 enum 값으로 두면 cron 외 호출은 모두 명시 태그.
- 운영 정책으로도 가능: 수동 호출은 항상 `--dry-run` 강제.

### B. 중기 — Lifecycle 강화

**B1. confirm_status 정의를 Enum으로 승격**
- 현재 string 필드(`'watchlist'`/`'passed'`/...). Python enum + DB constraint로 승격하면 typo/invalid value 차단.
- 5/6 외부 시스템이 enum 사용 (Freqtrade만 예외).

**B2. OrderStatus를 Nautilus 14-state로 확장 검토**
- 현재 `PENDING/SUBMITTED/PARTIALLY_FILLED/FILLED/CANCELED/REJECTED/EXPIRED` 7개 → `DENIED`(사전 거부), `ACCEPTED`(broker ack), `EMULATED`/`TRIGGERED`(local STOP) 추가 가치 있음. 특히 **`DENIED` 추가**가 9중 안전장치 가시화에 직접적.
- 효과: "왜 안 보냈는지" 추적 — 현재는 confirm_status='failed'에 사유가 있지만, 발송 전 거부된 케이스가 별도 view로 안 보임.

### C. 장기 — 아키텍처 정렬

**C1. 9중 안전장치를 `PreSubmitValidator` 체인으로 명시화**
- 현재 9개가 `intraday_confirm.py` 안에 절차 코드로 분산. Freqtrade `confirm_trade_entry` 또는 Nautilus `RiskEngine` 스타일로 각 validator를 객체화하면 (a) 디버그 용이 (b) 새 안전장치 추가 시 한 곳만 수정 (c) 거부 사유 구조화된 alert 가능.
- 비용 큼 — 우선순위는 A 적용 후.

**C2. Broker drift dashboard**
- 현재 reconciliation은 alert만. Hummingbot/Nautilus 패턴은 분 단위 broker vs cache diff 노출. Next.js `/comparison` 또는 새 페이지에 표시 + 5분 이상 drift 시 자동 trading PAUSE.

### 적용하지 말 것
- **NautilusTrader 전면 마이그레이션**: 메모리에 "NautilusTrader+Webull 어댑터" 언급 있지만 현재 실 운영은 daily_pipeline 자체 흐름 + AlpacaAdapter. 전체 ExecutionEngine을 들이는 건 ROI 낮음.
- **자체 reconciliation 엔진 재작성**: 현재 daily 1회 + alert 수준이 paper 운영에는 충분.

---

## 4. Verification (권장사항 채택 시 검증)

| 권장사항 | 검증 방법 |
|---|---|
| A1 client_order_id prefix | 발송 1건 후 Alpaca API `get_orders`로 `client_order_id` 포맷 확인. grep으로 5/8 종류 사고 재현시 발신 경로 즉시 찾는지 검증 |
| A2 manual_oneshot enum 추가 | alembic migration 적용 + 수동 `--phase trade` 호출 시 새 enum 값 입력 검증 |
| B1 confirm_status enum | DB constraint 위반 케이스 강제 입력 → reject 확인 |
| B2 DENIED 추가 | 9중 안전장치 중 하나 일부러 트리거 → DENIED 레코드 생성 확인 |
| C1 PreSubmitValidator 체인 | unit test로 각 validator 독립 검증, alert에 사유 포함 |
| C2 drift dashboard | 일부러 broker 직접 cancel 후 5분 내 diff 표시 확인 |

---

## Critical files (현재 상태 — 미수정)

- `scripts/daily_pipeline.py:78-128, 132-270, 343-450, 462-722`
- `scripts/intraday_confirm.py:111-450`
- `broker_adapter/alpaca_adapter.py:138-227`
- `api/db/models.py:38-46, 319-386`
- `api/routes/trading.py:626-706`
- `notifications/heartbeat.py:102-153`

## 출처 (직접 확인)

- Freqtrade: `freqtrade/persistence/trade_model.py`, docs `strategy-callbacks`
- Jesse: `jesse/models/Order.py` (`order_statuses`, `order_submitted_via`)
- Hummingbot: `hummingbot/core/data_type/in_flight_order.py` (`OrderState` IntEnum), Architecture blog Part 1
- NautilusTrader: docs `concepts/orders`, `concepts/execution`
- Lean: `Engine/TransactionHandlers/BrokerageTransactionHandler.cs`, docs `pre-trade-risk-control`
- Backtrader: `backtrader/order.py`, docs `docu/order`

미확인: Jesse live reconciliation 코드 path, Hummingbot manual order 채널(추정 미지원).
