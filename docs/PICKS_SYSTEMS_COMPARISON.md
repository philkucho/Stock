# Picks Systems Comparison

종목 선정 4-시스템 비교 (세로: 평가 기준 / 가로: 시스템).

**코드 위치:**
- `scanner/stage2_daily_picks.py`, `scanner/comparison/v3_historical.py` (v3)
- `scripts/scan_momentum.py` (scanner)
- `scanner/integrated/run.py::run_integrated_v10` (v10)
- `scanner/integrated/run.py::run_integrated_v9` (v9, fallback)

---

## 1. 개요

| 기준 | v3 | scanner | integrated v10 | integrated v9 |
|---|---|---|---|---|
| **정체성** | 퀄리티 셋업 필터 (Minervini) | 거래량+모멘텀 카운터 | v3+scanner 통합 메타 시스템 | v10 직전 세대 |
| **역할** | 단독 셋업 검증 | 후보 풀 공급 | 운영 picks (primary) | v10 fallback / 베이스라인 |
| **점수 범위** | 0~100 (5블록) | 0~6 (시그널 합) | ~0~250 (composite) | ~0~200 (composite) |
| **Universe** | UniverseMember (큐레이션) | DB 일봉 전종목 | v3 ∪ scanner | v3 ∪ scanner |
| **출력 수** | top 5~15 | top 20~30 | top 5 | top 5 |
| **데이터 의존** | 일봉 + 프리마켓 (운영) | 일봉 only | 일봉 + DB 메모리 | 일봉 + DB 메모리 |

---

## 2. 입력 / 데이터

| 기준 | v3 | scanner | v10 | v9 |
|---|---|---|---|---|
| 일봉 (400d) | ✅ | ✅ | ✅ | ✅ |
| 프리마켓 갭/RVOL | ✅ (운영) | ❌ | ❌ | ❌ |
| 5분봉 패턴 | ✅ (운영) | ❌ | ❌ | ❌ |
| Earnings calendar | ✅ catalyst | ✅ phase tag | ✅ multiplier+filter | ✅ multiplier+filter |
| Regime state | ✅ gate | ✅ on/off | ✅ gate+boost | ✅ gate+boost |
| Sector ETF bars | — | — | ✅ (momentum) | ✅ (momentum) |
| SystemPickLog (자기 픽 이력) | — | — | ✅ streak | ✅ streak |
| PickOutcome (feedback) | — | — | ✅ 30d cubic | ✅ 30d cubic |
| UniverseMember | ✅ (멤버십) | ❌ | 간접 (v3 통해) | 간접 (v3 통해) |

---

## 3. Hard Gate (탈락 조건)

| 기준 | v3 | scanner | v10 | v9 |
|---|---|---|---|---|
| Regime defensive 시 차단 | ✅ | ❌ (annotate only) | ✅ | ✅ |
| RSI grade=bad 탈락 | ❌ | ❌ | ✅ | ✅ |
| Open location gap_and_fail_risk 탈락 | ❌ (block_c 감점) | ❌ | ✅ | ✅ |
| Forward ER 0~7d 제외 | ❌ | ❌ | ✅ | ✅ |
| VIX>25 시 comp+exp 필수 | ❌ | ❌ | ✅ | ✅ |
| Drawdown mode conservative 필터 | ❌ | ❌ | ✅ | ❌ |
| Auto-blacklist (30d reject 2회+) | ❌ | ❌ | ✅ | ❌ |
| Feedback weighted_avg < -1% 거부 | ❌ | ❌ | ✅ | ✅ |
| Tier 2 — 9 시그널 중 3+ AND C/E 필수 | — | — | ✅ | ✅ |
| Score threshold | SCORE_THRESHOLD | min-score arg | composite top N | composite top N |

---

## 4. 스코어링 — 셋업 강도

| 기준 | v3 | scanner | v10 | v9 |
|---|---|---|---|---|
| Stage 2 trend template | ✅ block | — | ✅ +8 보너스 | ✅ +8 보너스 |
| RS percentile (vs peers) | ✅ block_a | — | — | — |
| RS 1m vs 3m (momentum accel) | — | — | ✅ +5 | ✅ +5 |
| Catalyst score | ✅ block | — | — (multiplier로 대체) | — |
| Premarket gap | ✅ block_b | — | — | — |
| RVOL | ✅ block_b | — | — | — |
| Volume trend | — | ✅ (1점) | 간접 (scanner score 통해) | 동일 |
| MA alignment | — | ✅ (1점) | 간접 | 동일 |
| RSI bullish | — | ✅ (1점) | — | — |
| MACD | — | ✅ (1점) | — | — |
| Above MA200 | — | ✅ (1점) | 간접 | 동일 |
| Breakout 20d | — | ✅ (1점) | 간접 | 동일 |
| Compression (BB squeeze) | — | — | ✅ score/6 * 20 | 동일 |
| Expansion (squeeze breakout) | — | — | ✅ +10 if comp+exp | 동일 |
| RSI structure (good/neutral) | — | — | ✅ score/5 * 5 | 동일 |
| Open location (피벗 대비) | ✅ block_c | — | ✅ score/5 * 8 | 동일 |
| OBV up | — | — | ✅ +5 | ✅ +5 |
| Anchored VWAP (5d) 위 | — | — | ✅ +5 | ✅ +5 |
| Sector priority (반도체/IT) | — | — | ✅ +10 | ✅ +10 |
| Sector momentum (vs SPY 5d) | — | — | ✅ +5 (>0.5%) | ✅ +5 |
| Confluence (v3+scanner≥4) | — | — | ✅ +15 | ✅ +15 |
| WHITELIST 가중 | ✅ | annotate | 간접 (v3 통해) | 동일 |

---

## 5. 스코어링 — 시계열 메모리

| 기준 | v3 | scanner | v10 | v9 |
|---|---|---|---|---|
| Streak bonus (v3 연속 픽) | — | — | ✅ 3+/5+ → +10/+15 | ✅ 동일 |
| Feedback decay (PickOutcome) | — | — | ✅ cubic 30d, ±12 cap | ✅ 동일 |
| Auto-blacklist | — | — | ✅ | ❌ |
| Drawdown-aware mode | — | — | ✅ | ❌ |
| Confluence super-multiplier ×1.3 | — | — | ✅ | ❌ |

**Feedback decay weight:** 1d ×4, 2-5d ×2, 6-15d ×1, 16-30d ×0.3

---

## 6. Multiplier / Penalty

| 기준 | v3 | scanner | v10 | v9 |
|---|---|---|---|---|
| Regime boost (aggressive) | — | — | ×1.2 (compression에만) | 동일 |
| PEAD earnings multiplier | catalyst 점수 | phase tag만 | ×1.25 (post) | ×1.25 |
| Super-multiplier (5-confluence) | — | — | ×1.3 | ❌ |
| Sector diversification penalty | compress_by_sector | — | -5/회 (3번째부터) | 동일 |
| Score penalties | ✅ (block 내) | — | — | — |

---

## 7. 성과 (60d backfill, 강세장)

| 기준 | v3 | scanner | v10 | v9 |
|---|---|---|---|---|
| 10d alpha | +7.66% | +0.14% | **+14.88%** | ~+12% (추정) |
| Win rate | — | — | **93%** | — |
| Sharpe | — | — | **6.58** | ~5~6 |
| 약세장 (2022 H1) | — | — | picks=0 (자본보존) | 동일 |

---

## 8. 운영

| 기준 | v3 | scanner | v10 | v9 |
|---|---|---|---|---|
| 호출 시점 | 09:25 ET cron | on-demand | 09:25 ET cron | v10 실패 시 |
| 결과 저장 | daily_picks 테이블 | stdout/JSON | daily_picks | daily_picks |
| SystemPickLog 기록 | ✅ (system_id="v3") | ❌ | ✅ ("integrated") | ✅ ("integrated") |
| 비교/backfill | v3_historical.py | scan with --date | backfill_integrated.py | 동일 |

---

## 9. 데이터 흐름

```
[ DB 일봉 ]
      │
      ├──► scanner (broad, 0~6) ──────┐
      │                                │
      └──► v3 universe ──► v3 게이트 ──┤
                          + 5블록 점수 │
                                       ▼
                          integrated v10 (union 후 재랭킹)
                          - 시그널 9종 추가 점수
                          - feedback/streak/blacklist 메모리
                          - hard filter (ER, VIX, 분산)
                          - super-multiplier ×1.3
                                       │
                                       ▼  v10 실패 시
                          integrated v9 (3개 기능 제거판)
```

---

## 10. 한 줄 사용 가이드

| 상황 | 쓰는 시스템 |
|---|---|
| 종목 한 개 setup 강도 검증 | **v3** |
| 오늘 시장 거래량 흐름 스캔 | **scanner** |
| 자동매매 daily picks (운영) | **integrated v10** |
| v10 회귀 검증 / A/B 베이스라인 | **integrated v9** |

---

## 11. v9 vs v10 상세 비교

v10 = v9 + (super-multiplier + auto-blacklist + drawdown-aware mode).
나머지 모든 시그널/가중치/필터는 **완전 동일**.

### 11.1 차이점 (v10 신규 3개)

| 기능 | v9 | v10 | 효과 |
|---|---|---|---|
| **Confluence super-multiplier** | ❌ | ✅ ×1.3 | 5개 강신호 동시 충족 시 composite ×1.3 부스트 |
| **Auto-blacklist** | ❌ | ✅ 30d window | feedback에서 alpha<-1% 2회+ 종목 영구 제외 |
| **Drawdown-aware mode** | ❌ | ✅ activate조건 | 최근 10d picks 중 5d alpha<-5% 2개+면 defensive 필터 ON |

### 11.2 Super-multiplier 작동 조건 (5개 동시 충족)

```
sm.score >= 4              # scanner도 강함
AND ce.is_compression       # Bollinger squeeze
AND ce.is_expansion         # squeeze breakout
AND streak_count >= 3       # v3가 3일+ 연속 픽
AND rsi.grade == "good"     # RSI 강세 구조
→ composite × 1.3
```
Tier 1(v3_priority), Tier 2(scanner) 둘 다 적용.

### 11.3 Auto-blacklist

- 30일 lookback에서 같은 symbol의 5d outcome 중 alpha < -1.0%인 건이 2회+
- → `auto_blacklist` set에 추가 → 이후 picks에서 영구 제외 (v3/scanner 둘 다)
- v9은 같은 데이터로 "이번 한 번만 거부"하지만 다음날 다시 후보로 들어옴

### 11.4 Drawdown-aware mode

- Trigger: `SystemPickLog.system_id="integrated"`의 최근 10d 결과 중 5d alpha<-5%가 2개+
- ON 시 추가 필터: `compression OR rsi=good OR avwap_above` 중 1개 필수
- 트리거 안 되면 v9과 동일하게 작동

### 11.5 composite 점수 계산 차이

**v9 (Tier 1):**
```
composite = base × earnings_mult
```

**v10 (Tier 1):**
```
composite = base × earnings_mult × super_mult
                                   └─ 1.3 if 5-confluence else 1.0
```
base 산식은 동일. v10은 v9 점수 위에 **×1.3 부스트 게이트**만 얹은 구조.

### 11.6 효과 — 어떻게 다르게 작동하나

| 시나리오 | v9 결과 | v10 결과 |
|---|---|---|
| 강한 셋업 1개 + 약한 셋업 4개 | 강한 게 top일 가능성 높음 | 강한 게 ×1.3 → 더 확실히 top |
| 한 번 -3% 손실 종목이 며칠 후 재등장 | feedback 점수 -로 약화되지만 후보 유지 | 2회+ -1% 이상이면 영구 제외 |
| 최근 며칠 큰 손실 (drawdown) | 그대로 진행 | conservative 필터 자동 ON |
| 평범한 강세장 | 동일 결과 | 동일 결과 (3개 기능 모두 비활성) |

→ **v10은 강세장에선 v9과 거의 동일**, 시장 악화나 시그널 confluence가 강할 때만 차별화.

### 11.7 코드 위치 차이 (line 기준)

| 항목 | v10 | v9 |
|---|---|---|
| 함수 시작 | `run.py:208` | `run.py:660` |
| auto_blacklist 계산 | `:308-315` | 없음 |
| drawdown_mode 트리거 | `:317-331` | 없음 |
| `_drawdown_mode_check` | `:459-463` | 없음 |
| super_confluence 게이트 (T1) | `:507-513` | 없음 |
| super_mult 적용 | `:518` | 없음 |
| super_confluence (T2) | `:594-598` | 없음 |

나머지 코드는 거의 1:1 mirror.

### 11.8 한 줄 정리

- **v9** = "메타 시그널 통합 + 시계열 feedback" 베이스라인
- **v10** = v9 + "강한 셋업 부스트 + 자기 보호 메모리(blacklist) + 자기 보호 모드(drawdown)"

v9까지가 **alpha generation**, v10이 추가한 3개는 **alpha protection** 성격.
