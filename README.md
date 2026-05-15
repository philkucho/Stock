# US Stock Automated Trading System

NautilusTrader + Webull OpenAPI 기반 미국주식 자동매매 시스템.

## 아키텍처

```
[Next.js 대시보드] ←→ [FastAPI] ←→ [NautilusTrader 엔진] ←→ [webull_adapter] ←→ [Webull OpenAPI]
                                          ↑                            ↑
                                     [전략 코드]              [Webull Python SDK]
                                          ↑
                                  [yfinance 백테스트 데이터]
```

## 디렉토리

| 경로 | 역할 |
|------|------|
| `webull_adapter/` | NautilusTrader용 Webull 브로커 어댑터 (직접 구현) |
| `strategies/` | 매매 전략 코드 |
| `backtests/` | 백테스트 실행 스크립트 |
| `api/` | FastAPI 백엔드 (대시보드 ↔ 엔진 연결) |
| `frontend/` | Next.js 대시보드 |
| `notebooks/` | 탐색/실험용 Jupyter 노트북 |
| `alembic/` | DB 스키마 마이그레이션 (Postgres + TimescaleDB) |
| `api/db/` | SQLAlchemy 모델 / async 세션 |
| `data/` | 로컬 캐시 등 (gitignore) |

## 셋업

### 0. 사전 요구사항

- **Python 3.11 필수** (Webull SDK가 `grpcio==1.51.1`에 핀고정되어 있어 3.12+ wheel 없음. 3.10 이하는 NautilusTrader 미지원)
  - https://www.python.org/downloads/ 에서 설치
  - 설치 시 "Add Python to PATH" 체크 필수
- **Node.js 20+** (Next.js용, 프론트엔드 작업 시)
- **Docker Desktop** (Postgres + TimescaleDB 컨테이너 실행용)
- **Webull App Key/Secret** (https://developer.webull.com 신청, 1~2 영업일 승인)

### 1. Python 가상환경 생성

```powershell
# Windows PowerShell (프로젝트 루트에서)
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. 의존성 설치

```powershell
pip install --upgrade pip
pip install -e ".[dev]"
```

### 3. 환경 변수 설정

```powershell
copy .env.example .env
# .env 파일을 열어 WEBULL_APP_KEY, WEBULL_APP_SECRET 입력
```

### 4. DB 기동 + 마이그레이션

```powershell
# Postgres + TimescaleDB 컨테이너 시작 (백그라운드)
docker compose up -d

# 스키마 적용
alembic upgrade head

# 새 모델 추가 후 마이그레이션 생성 (변경이 생겼을 때만)
alembic revision --autogenerate -m "describe change"
```

### 5. 동작 확인 (smoke test)

```powershell
# Jupyter 노트북 실행
jupyter notebook notebooks/01_smoke.ipynb
```

### 6. FastAPI 백엔드 실행

```powershell
uvicorn api.main:app --reload --port 8000
# 브라우저에서 http://127.0.0.1:8000/docs 확인
```

### 7. Next.js 프론트엔드 (추후 추가)

```powershell
cd frontend
npm install
npm run dev
# 브라우저에서 http://localhost:3000 확인
```

## 개발 흐름

| 단계 | 산출물 | 검증 |
|------|--------|------|
| Day 1 | venv + 의존성 + smoke 노트북 동작 | `import nautilus_trader` 성공, Webull SDK로 잔고 조회 성공 |
| Day 2~3 | IB 어댑터 학습 노트, webull_adapter 스켈레톤 | 본인 노트, 빈 클래스 골격 |
| Day 4~5 | WebullInstrumentProvider, WebullDataClient (read-only) | 단일 종목 시세 1회 조회 |
| Day 6 | WebullExecutionClient 골격 (read-only) | paper 계좌 잔고 NautilusTrader 노드에서 조회 |
| Day 7 | MQTT 시세 스트림 + FastAPI 잔고 엔드포인트 + Next.js 스켈레톤 | `curl localhost:8000/api/account` 동작 |
| 2주차 | `_submit_order` + paper trading에서 첫 매매 | paper 계좌 1주 매수→매도 1회 |
| 3주차+ | 전략 백테스트, 손절/익절, 소액 라이브 검증 | $100 라이브 1주 거래 1회 |

## 핵심 함정

- **Webull rate limit**: Place Order 1초당 1건. 고빈도 전략 불가
- **Paper trading 한계**: 슬리피지 없음 → 라이브와 동작 차이. 소액 라이브 검증 필수
- **3개 프로토콜**: HTTP + MQTT + GRPC 동시 사용. 백그라운드 태스크 관리 필요
- **OAuth 토큰 만료**: 자동 재인증 루프 필수
- **Look-ahead bias**: 백테스트에서 미래 데이터 누출 금지. 모든 지표는 `t-1` 시점 기준

## 라이센스

NautilusTrader 의존성으로 인해 LGPL-3.0 영향 받음. 개인 운영은 무리 없음. 외부 배포 시 어댑터 부분 소스 공개 의무.
