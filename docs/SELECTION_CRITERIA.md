# 종목 선정 기준 v2 — 단타·스윙 Hybrid

**문서 위치**: `docs/SELECTION_CRITERIA.md`
**버전**: v2.0 (2026-05-07)
**상태**: 초안 — 사용자 리뷰 대기
**대상**: `scanner/stage2_daily_picks.py` 점수표 재설계

---

## 1. 배경 — 왜 v2가 필요한가

기존 v1 점수표 (24점)는 다음 한계가 드러남:

| 문제 | 사례 |
|---|---|
| **갭 의존도 과대 (S1 7점)** | ZS 0.77× 거래량에도 +8.36% 갭만으로 Top 1 (선정됨에도 거래량 부족) |
| **RSI 점수 미반영** | VRT RSI 74.8 (과매수)에도 -2점 페널티가 임계값 75에 못 미쳐 적용 안 됨 |
| **거래량은 게이트만, 점수 없음** | 0.7× 컷이 단편적, 평균 이상 종목 가산점 없음 |
| **누락 핵심 팩터** | ATR (변동성), Relative Strength vs SPY, Float, MA Alignment, Stage 2 Trend Template |
| **multi-timeframe 통합 없음** | 주봉·일봉·분봉 신호가 분리되어 시너지 안 남 |
| **Sector 매핑 불완전** | Industrials, Consumer Discretionary 등 ETF 미정의 |

리서치 기반 v2는 **전문가 6 프레임워크 + 학술 4개 팩터 모델**을 종합한 100점 기반 점수표 + 다단 게이트로 재설계.

---

## 2. 리서치 토대

### 2-A. 단타·스윙 고수 6 프레임워크

| # | 프레임워크 | 핵심 기준 (수치 임계값) | 시간 지평 |
|---|---|---|---|
| 1 | **Minervini SEPA + Trend Template** | 8조건(가격>50/150/200MA, 50>150>200, 200MA 1개월+ 상승, 52w저↑+30%, 52w고↓-25% 이내, **RS≥80**) + VCP + EPS YoY≥25% + ROE≥17% | 스윙·중기 |
| 2 | **O'Neil CAN SLIM + IBD Composite** | C(EPS YoY ≥+25%), A(연 EPS≥25%, ROE≥17%), N(신고가/신제품), S(소형 float, 거래량 ≥평균140%), L(**RS≥80**), I(기관 보유 증가), M(시장 Confirmed Uptrend) | 스윙·중기 |
| 3 | **Cameron Gap & Go (Warrior)** | 5 Pillars: Gap≥4%, **Float<20M**, Price $2~$20, News, **RVOL≥5×** (프리마켓 ≥50K) | 단타 |
| 4 | **Stockbee 4% Momentum Burst** | c/c1>1.04, v>v1, V>100K, **Float<25M**, NR(narrow range) 3~20일 후 burst, 첫·둘째 burst만 (extended 회피) | 스윙 (3-5일) |
| 5 | **Aziz "Stocks in Play"** | Gap≥±2%, 일평균 거래량≥500K, 프리마켓≥50K, **ATR≥$0.50**, News, Price $1~$50, ABCD/Bull Flag | 단타 |
| 6 | **Zanger Pattern Trading** | Cup&Handle/Bull Flag/Pennant, **Float<100M**, 돌파일 거래량 ≥평균+50% (이상+300%), 시장·업종 동조, 돌파선 ±5% 이내 | 스윙 |

### 2-B. 학술 팩터 모델

| 모델 | 핵심 변수 | 단타·스윙 변환 |
|---|---|---|
| **Fama-French 5** | Mkt-Rf, SMB, HML, **RMW**(profitability), **CMA**(investment) | 30종목 universe → z-score 백분위 사용 |
| **AQR QMJ** | Profitability + Growth + Safety + Payout (각 z-score) | Quality bottom 30% 제거 (음의 알파 회피) |
| **Jegadeesh-Titman 12-1 MOM** | `P_{t-21} / P_{t-252} − 1` | 단기: 3-1 momentum + 20거래일 RS |
| **AQR QARP** | Value × Momentum × Quality 3중 z-score | 메인 스코어 골격 |

### 2-C. IBD Ratings (실무자 표준 산식)

```
RS_raw = 2 × (Close/Close_{63d}) + (Close/Close_{126d})
       + (Close/Close_{189d}) + (Close/Close_{252d})
RS_Rating = percentile_rank(RS_raw, all stocks)  # 1~99
```

- **Composite Rating** = 2×EPS Rating + 2×RS Rating + Industry Group RS + SMR + Acc/Dist + 52W high distance → 1~99 percentile
- 임계값: **Composite ≥ 90**, 핵심 진입 ≥ 95
- 우리 universe(30종목)는 절대 RS 대신 **유니버스 + SPY/QQQ 포함 percentile**로 대체

### 2-D. 6 프레임워크 합의 — 공통 핵심 팩터 Top 10

| 순 | 팩터 | 합의 임계값 | 동조 프레임워크 |
|---|---|---|---|
| 1 | RVOL | ≥1.5× 스윙, ≥5× 단타 | All 6 |
| 2 | Relative Strength (vs SPY) | RS Rating ≥80 / 1·3개월 outperform | Minervini, O'Neil, Zanger |
| 3 | Float | <20M 단타, <100M 스윙, 메가캡 회피 | Cameron, Bonde, Aziz, Zanger, O'Neil |
| 4 | Gap % | ≥2% 일반, ≥4% 단타 강자 | Cameron, Bonde, Aziz |
| 5 | News Catalyst | ER/FDA/M&A/계약 | Cameron, Aziz, O'Neil |
| 6 | 52w 고가 근접도 | 고가 대비 -25% 이내 (이상 -15%) | Minervini, O'Neil, Zanger |
| 7 | MA 정렬 | 가격 > 50 > 150 > 200, 200MA 우상향 | Minervini, O'Neil, Zanger |
| 8 | Base/Pattern 정돈도 | VCP, Cup&Handle, Flag, NR 3-20일 | Minervini, O'Neil, Bonde, Zanger, Aziz |
| 9 | ATR | ≥$0.50 또는 가격의 3-5% | Aziz, Cameron |
| 10 | 시장 + 업종 강세 | "Confirmed Uptrend" + 업종 RS 상위 20% | O'Neil, Zanger, Minervini |

### 2-E. Multi-timeframe 통합 — Elder Triple Screen

1. **Tide (주봉)** — 추세 필터: 26주 EMA 기울기 + MACD-Histogram 부호. 양(+)일 때만 long
2. **Wave (일봉)** — 역추세 오실레이터: Force Index, Stochastic, Williams %R이 oversold일 때 setup
3. **Ripple (인트라데이 1~5분봉)** — trailing buy-stop = 전일 고점 + 0.1×ATR

---

## 3. v2 선정 시스템 — 2-Tier 구조

```
┌────────────────────────────────────────────────────────────┐
│ Tier 1: Hard Gates (12개) — 하나라도 fail 시 즉시 탈락       │
└────────────────────────────────────────────────────────────┘
                        ↓
┌────────────────────────────────────────────────────────────┐
│ Tier 2: Score (100점) — 4 Block + Penalty                 │
│   A. Trend & RS (40)                                        │
│   B. Catalyst & Volume (25)                                 │
│   C. Setup Quality (20)                                     │
│   D. Risk & Sector (15)                                     │
│   ─ Penalties (-15까지 감점)                                │
└────────────────────────────────────────────────────────────┘
                        ↓
┌────────────────────────────────────────────────────────────┐
│ Cut: ≥60점 후보. 상위 5 → 섹터 압축 → Top 3 + 백업 2          │
└────────────────────────────────────────────────────────────┘
```

### 3-A. Tier 1: Hard Gates (12개)

각 게이트는 **boolean**. 하나라도 false면 후보 탈락.

| ID | 게이트 | 임계값 | 데이터 소스 | 근거 |
|---|---|---|---|---|
| **G1** | 시장 환경 | QQQ 일봉 close > 20EMA **AND** 프리마켓 갭 > -1% | yfinance | O'Neil "Confirmed Uptrend" |
| **G2** | Universe 멤버 | `universe_members.enabled = true` | DB | Stage 1 (월간) |
| **G3** | 유동성 | 평균 30일 일거래대금 ≥ $20M | yfinance daily | Aziz, Zanger |
| **G4** | Float | 5M ≤ float ≤ 5B | yfinance `info.floatShares` | 단타·스윙 양립 (Ross<20M, Zanger<100M, 메가캡 회피) |
| **G5** | 가격대 | $5 ≤ 가격 ≤ $500 | yfinance | 페니주 + 1주에 큰 자본 묶이는 종목 회피 |
| **G6** | ER 회피 | 당일 ±1일 실적발표 아님 | yfinance.calendar | 단타 변동성 폭발 회피 |
| **G7** | Halt 이력 | 최근 5거래일 halt 0회 | (TODO Phase 2) NYSE/NASDAQ feed | 거래정지 위험 회피 |
| **G8** | 스프레드 | bid-ask ≤ 0.3% (가격×3bp) | yfinance bid/ask | 슬리피지 방지 |
| **G9** | ATR 범위 | 일봉 ATR(14) ≥ 가격의 1.5% **AND** ≤ 12% | 자체 산출 | RR 확보 + 변동성 폭주 회피 |
| **G10** | 거래량 sanity | 전일 거래량 / 20d평균 ≥ 0.7 (lenient 모드) **OR** 프리마켓 거래대금 ≥ $5M | 자체 산출 | 신뢰성 거래량 확인 |
| **G11** | Stage 2 Trend Template | (스윙 후보만) Minervini 8조건 통과 | 자체 산출 | 약세장 종목 자동 제거 |
| **G12** | 카탈리스트 (단타만) | News/ER/FDA/Upgrade 중 1개 이상 | Finnhub + EarningsCal + PR Newswire | 단타는 카탈리스트 필수 |

**G11 vs G12 분기**: 종목별로 `intended_horizon` 결정 후 적용
- ATR < 가격의 3% → 스윙 후보 → G11 적용 (G12 건너뜀)
- ATR ≥ 가격의 3% → 단타 후보 → G12 적용 (G11 건너뜀)

### 3-B. Tier 2: Score (100점)

#### Block A — Trend & Relative Strength (40점)

| 항목 | 만점 | 산식 | 근거 |
|---|---|---|---|
| **A1 RS (IBD-style 4-quarter)** | 15 | `2×P/P_{63} + P/P_{126} + P/P_{189} + P/P_{252}` → universe+SPY+QQQ percentile.<br/> 0~50 percentile = 0점, 50~70 = 5, 70~85 = 10, 85+ = 15 | O'Neil 공식, Minervini RS≥80 |
| **A2 Multi-timeframe MOM** | 10 | 1m(21d) + 3m(63d) + 6m(126d) RS vs SPY 모두 양수 = 10. 2개 양수 = 6. 1개 양수 = 3. 모두 음수 = 0 | Jegadeesh-Titman, AQR Momentum |
| **A3 Stage 2 강도** | 15 | 8조건 통과 = 12점.<br/> +52w 고점 거리 -3% 이내 = +2.<br/> +200MA 기울기 강함 (1개월 ≥+5%) = +1 | Minervini Trend Template |

#### Block B — Catalyst & Volume (25점)

| 항목 | 만점 | 산식 | 근거 |
|---|---|---|---|
| **B1 RVOL** | 10 | `min(10, 2 × log2(RVOL+1))`. RVOL=1×→2점, 2×→3.2, 5×→5.2, 10×→6.9, 32×→10. 프리마켓 RVOL 우선, 없으면 일봉 거래량 비율 | Cameron, Bonde, Warrior |
| **B2 거래량 surge** | 5 | 전일 거래량 ÷ 20d 평균: <0.7 = -2 (페널티 풀에서), 0.7~1.3 = 0, 1.3~2 = 3, ≥2 = 5 | Zanger 평균+50%, 일반 합의 |
| **B3 카탈리스트 강도** | 10 | ER 당일/D-1 = 10, FDA/M&A = 8, Analyst Upgrade = 5, Sector뉴스 = 3, 일반뉴스 = 1, 없음 = 0 | Cameron "News" |

#### Block C — Setup Quality (20점)

| 항목 | 만점 | 산식 | 근거 |
|---|---|---|---|
| **C1 Pattern detected** | 10 | `tight_flag_setup` (5분봉) = 10. Bull Flag (3-5봉 깃대 컨솔) = 8. 20일 신고가 돌파 = 6. 베이스 형성중 = 3. 없음 = 0 | Aziz, Zanger, Bonde |
| **C2 Pivot 근접도** | 5 | (가격 - 피벗) / 피벗 distance: ±0.5% = 5, 0.5~2% = 3, 2~5% = 1, >5% = -3 (extended 페널티) | Zanger ±5% 이내 |
| **C3 Base duration** | 5 | 직전 NR 컨솔 일수: 3~20일 = 5, 1~2일 = 2, 21~50일 = 3, >50일 = 0 (후기 stage) | Stockbee NR rule, Minervini stage |

#### Block D — Risk & Sector (15점)

| 항목 | 만점 | 산식 | 근거 |
|---|---|---|---|
| **D1 RSI(14) bands** | 5 | 50~70 = 5, 30 미만 = 4(반등 후보), 70~80 = 0, **>80 = -3 (블록 전체 합산 시)**, 그 외 = 2 | RSI 표준 + 단타 추격 회피 |
| **D2 Beta sweet spot** | 3 | 1.0~2.0 = 3, 0.7~1.0 또는 2.0~2.5 = 2, <0.7 또는 >2.5 = 1 | 단타 모멘텀 베타 |
| **D3 ATR-implied RR** | 3 | (피벗-진입) ÷ ATR(14): ≥3 = 3, 2~3 = 2, 1~2 = 1, <1 = 0 | Aziz ATR-based stop |
| **D4 Sector strength** | 4 | 섹터 ETF가 같은 방향 + RS 상위 30% = 4. 같은 방향만 = 2. 반대 방향 = 0. 섹터 RS 하위 30% = -1 | Zanger group strength |

#### Penalty Pool (-15까지 감점)

| 항목 | 감점 | 조건 |
|---|---|---|
| P1 거래량 부족 | -5 | 일봉 vol_ratio < 0.7 (G10 통과 시도, 게이트 lenient 모드 적용 시) |
| P2 Climax run | -5 | 7주 연속 상승 후 (extended) |
| P3 Short Squeeze 양면 위험 | -5 | Short Interest > 40% AND Days to Cover > 5 |
| P4 Pivot extended | -3 | 가격이 피벗선 +5% 이상 (추격 영역) |
| P5 RSI 극과 매수 | -3 | RSI > 80 (단기 폭발 후 조정 위험) |

### 3-C. 컷 + 선정

- 후보 컷: **총점 ≥ 60점**
- Top 5 추출 → 섹터 중복 압축 → **Top 3 + 백업 2**
- Lenient 모드 (장 마감 후 demo) 컷: **≥ 40점**

---

## 4. 실패 모드 — Gate 무력화/우회

| 시나리오 | 영향 | 방어책 |
|---|---|---|
| 약세장 (QQQ 200MA 하향) | G1 fail → 모든 후보 탈락 | "약세장 모드" — A1·A3 가중치 낮추고 D1 oversold 가산 |
| 후기 베이스 (3rd/4th stage) | C3에서 51일+ 컨솔 → 0점 | OK, 의도적 제외 |
| Earnings season 집중 | G6에서 다수 탈락 | universe 후보 풀이 좁아짐 → 백업 후보 풀 자동 확장 |
| Universe 너무 작음 (n<10) | RS percentile 신뢰성↓ | universe + SPY + QQQ + 섹터 ETF 합쳐서 percentile |
| 데이터 누락 (특정 종목 yfinance fail) | NaN 점수 → 컷 미달 | 0점 처리하되 로그 경고 |

---

## 5. 데이터 소스 매핑 (구현 가능성)

| 항목 | 무료 (yfinance) | 무료 (Finnhub free 60/min) | 유료 (Polygon Starter $29) |
|---|---|---|---|
| OHLCV 일봉 | ✓ | ✓ | ✓ |
| OHLCV 1분봉 | △ (지연·미흡) | — | ✓ (실시간 정확) |
| Float | ✓ (`info.floatShares`) | — | ✓ |
| ATR/RSI/MA | ✓ (자체 계산) | — | ✓ |
| RS Rating | ✓ (자체 산출) | — | ✓ |
| Earnings calendar | ✓ (`Ticker.calendar`) | ✓ (`/calendar/earnings`) | ✓ |
| News headlines | △ (limited) | ✓ (`/company-news`) | ✓ |
| **Short Interest** | ✗ | △ (간접) | △ (별도 API) |
| **IV Rank** | ✗ | ✗ | ✗ (옵션 데이터) |
| Sector classification | ✓ (`info.sector`) | ✓ | ✓ |
| Bid/Ask realtime | △ (15분 지연) | — | ✓ |

**MVP (Phase 1) 구현 범위:**
- ✓ Block A 전체 (15+10+15)
- ✓ Block B1·B2 + B3 (Finnhub/PRNewswire/EarningsCal로 부분 구현)
- ✓ Block C 전체 (자체 신호로 산출)
- ✓ Block D1·D2·D3·D4
- △ P3 Short Squeeze: **Phase 2로 보류** (free 소스 한계)
- △ G7 Halt 이력: **Phase 2로 보류** (실시간 feed 필요)

**Phase 2 (Polygon 도입 후) 추가:**
- 정확한 1분봉 VWAP, anchored VWAP
- IV Rank (옵션 데이터)
- Halt feed
- 정확한 bid/ask 스프레드

---

## 6. 구현 계획 (승인 시)

### 단계
1. **모델 변경**: `score_breakdown` JSONB에 새 점수 블록 (`block_a`, `block_b`, `block_c`, `block_d`, `penalties`) 추가. `total_score` Numeric(5,2) 그대로 (0~100).
2. **새 시그널 모듈**: `signals/relative_strength.py` (IBD 산식), `signals/stage2_trend_template.py` (Minervini 8조건), `signals/atr.py`
3. **`scanner/stage2_daily_picks.py` 재작성**:
   - `evaluate_gates_v2()`: 12 게이트
   - `evaluate_scores_v2()`: 4 Block + Penalty
   - `compute_pick_metadata_v2()`: ATR 기반 stop + R-multiple
4. **유니버스 확장**: 비교 데이터셋 (SPY, QQQ, 섹터 ETF 8개) 자동 fetch · 캐시
5. **Frontend**:
   - 점수 카드: 4 Block 색상 분리 (Trend=파랑, Catalyst=노랑, Setup=초록, Risk=주황)
   - 페널티 표시 (감점 항목 적색)
   - "왜 이 종목인가?" 자동 생성 로직 v2 — 각 Block 강세 항목 1줄씩
6. **테스트**:
   - 기존 23개 + Block별 단위 테스트 ~30개 추가
   - Backtest harness: 2024-2026 일봉 데이터로 v1 vs v2 비교
7. **마이그레이션**: 기존 `daily_picks` 호환 — 새 `score_breakdown` 구조는 JSONB라 schema 변경 없음

### 추정 작업량
- 신호 3개 추가: 2시간
- Gate/Score v2 함수 작성: 3시간
- 테스트: 2시간
- 프론트엔드 카드 v2: 2시간
- 백테스트 비교: 2시간
- **합계: ~11시간 (1~2일)**

---

## 7. 검증 계획

### 7-A. 정량 검증
1. **백테스트 비교 (v1 vs v2)**:
   - 2024-2026 일봉 데이터로 매일 picks 시뮬레이션
   - 다음 날 시가→종가 수익률, 5일 보유 수익률 측정
   - 비교 메트릭: win rate, R expectancy, MaxDD, Sharpe
   - **합격 기준**: v2가 win rate +5%p OR R expectancy +0.1R 이상 개선
2. **Score 분포 확인**:
   - 32종목 universe에서 점수 분포 (60점 컷 통과 비율 5~30%이 정상)
   - 모든 종목 100점 = scoring broken, 0종목 통과 = threshold 과다

### 7-B. 정성 검증
- Top 픽이 **납득 가능한 이유**로 선정되었는지 (전문가 룰 동조)
- VRT (RSI 74.8) 같은 케이스가 v2에서는 페널티 받아 탈락하는지
- ZS (vol 0.77) 같은 케이스가 v2에서는 점수 컷 미달 또는 백업으로 강등되는지

### 7-C. 운영 가드
- **A/B 모드**: v1·v2 picks를 동시 산출, 30일 paper에서 실 outcome 추적
- **자동 fallback**: v2가 0종목 → v1 사용 (graceful degradation)

---

## 8. 미해결 / 의사결정 필요 사항

| 항목 | 옵션 A | 옵션 B |
|---|---|---|
| **단타 vs 스윙 분기** | ATR 기준 자동 분기 (제안) | 사용자가 종목별 명시 |
| **Block 가중치** | A=40 / B=25 / C=20 / D=15 (제안) | 균등 25/25/25/25 |
| **컷오프 60** | 제안 | 50 또는 70 |
| **Short Interest** | Phase 2 보류 (제안) | 지금 필수, Polygon 도입 |
| **Stage 2 Template** | 모든 후보 적용 | 스윙 후보에만 적용 (제안) |
| **약세장 모드 자동 전환** | 제안 (QQQ 200MA 기준) | 수동 토글 |

---

## 9. 결론 — 사용자 결정 요청

이 설계서는 6개 단타·스윙 프레임워크 + 4개 학술 팩터 모델 + IBD 표준을 종합한 v2 선정 시스템 제안입니다.

**리뷰 시 확인할 핵심**:
1. Block 가중치 (A 40 / B 25 / C 20 / D 15) 동의?
2. 12개 Gate 항목 모두 채택? 또는 일부 보류?
3. Penalty 5개 모두 채택?
4. 미해결 결정 사항 (Section 8) 6개 답변
5. Phase 1 즉시 구현 vs Phase 2까지 상세 계획 후 시작?

승인 후 구현 단계로 이동.
