from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide, name="order_side"))
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType, name="order_type"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.PENDING, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    fills: Mapped[list[Fill]] = relationship(back_populates="order", cascade="all, delete-orphan")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    broker_fill_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide, name="order_side"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship(back_populates="fills")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (Index("ix_positions_symbol_account", "symbol", "account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    avg_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Bar(Base):
    """OHLCV 시계열. TimescaleDB hypertable로 저장 (마이그레이션에서 변환).

    Composite PK는 (time, symbol, interval). time이 첫 컬럼이어야 hypertable로 변환 가능.
    interval 예: "1d", "1h", "5m", "1m".
    """

    __tablename__ = "bars"
    __table_args__ = (
        Index("ix_bars_symbol_time", "symbol", "time"),
    )

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))  # "yfinance" | "webull" 등


class BacktestRun(Base):
    """백테스트 1회 실행 기록.

    strategy_params와 metrics는 JSONB로 유연하게 저장 (전략별 파라미터 다를 수 있음).
    params_hash: idempotency 키. (symbol, strategy, period, params)의 md5.
    """

    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("ix_backtest_runs_strategy_symbol", "strategy_name", "symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(64), index=True)
    strategy_params: Mapped[dict] = mapped_column(JSONB, default=dict)
    params_hash: Mapped[str | None] = mapped_column(String(32), unique=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    interval: Mapped[str] = mapped_column(String(8))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_source: Mapped[str] = mapped_column(String(16))  # "yfinance" | "db"

    starting_cash: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    final_equity: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    total_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 2))

    total_fills: Mapped[int] = mapped_column(Integer, default=0)
    total_positions: Mapped[int] = mapped_column(Integer, default=0)  # closed positions
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0"))

    # 추가 메트릭 (sharpe, max_drawdown 등) — 나중에 확장
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class UniverseMember(Base):
    """Stage 1 Universe — 단타 후보 풀 (월 1회 갱신, 30일 TTL).

    source 예: "score5_whitelist" / "momentum_scanner" / "manual" / "blacklist".
    valid_until 만료 시 rolling_revalidate()에서 재검증 통과 못하면 enabled=False.
    blacklist 멤버는 source="blacklist", enabled=True로 유지 (Stage 2가 제외 필터로 사용).
    """

    __tablename__ = "universe_members"
    __table_args__ = (
        UniqueConstraint("symbol", "source", name="uq_universe_symbol_source"),
        Index("ix_universe_enabled_valid", "enabled", "valid_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    source: Mapped[str] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(32))  # "semis_equipment" 등
    base_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    valid_until: Mapped[date | None] = mapped_column(Date)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_revalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)  # 섹터, market_cap, float, notes
    notes: Mapped[str | None] = mapped_column(String(500))


class DailyPick(Base):
    """Stage 2 Daily Picks — 매일 08:55 ET 자동 산출되는 Top 3 + 백업 2.

    rank 1~3 = top, 4~5 = backup. is_backup도 같은 정보 (편의용).
    gate_results / score_breakdown은 JSONB로 디버깅·저널 용도 보존.
    """

    __tablename__ = "daily_picks"
    __table_args__ = (
        UniqueConstraint("pick_date", "symbol", name="uq_daily_picks_date_symbol"),
        Index("ix_daily_picks_date_rank", "pick_date", "rank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    is_backup: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    gate_results: Mapped[dict] = mapped_column(JSONB, default=dict)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)

    pivot_price: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    stop_price: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    target_1r: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    target_2r: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    risk_per_share: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    position_size: Mapped[int] = mapped_column(Integer, default=0)

    strategy_tag: Mapped[str] = mapped_column(String(8), default="day")  # "day" | "swing"
    catalyst_summary: Mapped[str | None] = mapped_column(String(500))
    catalyst_source: Mapped[str | None] = mapped_column(String(64))
    market_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    sector: Mapped[str | None] = mapped_column(String(48))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SystemPickLog(Base):
    """3 시스템(v3 / scanner / integrated)이 산출한 일자별 top picks 통합 로그.

    각 시스템의 picks를 동일 스키마로 추적하여 1d/5d/10d 후 실현 수익을 비교.
    `score_meta` JSONB에 시스템별 다른 점수 데이터 저장 (v3 5-block, scanner 6-signal 등).
    """

    __tablename__ = "system_pick_logs"
    __table_args__ = (
        UniqueConstraint("system_id", "pick_date", "symbol", name="uq_pick_log_sys_date_sym"),
        Index("ix_pick_log_date_system", "pick_date", "system_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    system_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    pick_date: Mapped[date] = mapped_column(Date, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"))
    score_meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    sector: Mapped[str | None] = mapped_column(String(48))
    strategy_tag: Mapped[str] = mapped_column(String(8), default="swing")
    # 진입가 (다음날 시초가, 16:30 백필 시 채움)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    # 시뮬레이션 자본 균등 배분 — 각 시스템 $10,000 / 5 = $2,000
    sim_capital_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("2000"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    outcomes: Mapped[list[PickOutcome]] = relationship(
        back_populates="pick_log", cascade="all, delete-orphan"
    )


class PickOutcome(Base):
    """다중 지평(1d/5d/10d) 실현 수익 + SPY 알파.

    매일 16:30 ET cron이 백필. 5d면 entry_date+5거래일, 10d면 +10.
    """

    __tablename__ = "pick_outcomes"
    __table_args__ = (
        UniqueConstraint("pick_log_id", "horizon_days", name="uq_outcome_pick_horizon"),
        Index("ix_outcome_horizon_date", "horizon_days", "exit_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_log_id: Mapped[int] = mapped_column(
        ForeignKey("system_pick_logs.id", ondelete="CASCADE"), index=True
    )
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 5, 10
    exit_date: Mapped[date] = mapped_column(Date, nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    pct_return: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)  # %
    spy_pct_return: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    alpha: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)  # pct - spy_pct
    win_simple: Mapped[bool] = mapped_column(Boolean, default=False)  # pct > 0
    win_alpha: Mapped[bool] = mapped_column(Boolean, default=False)  # alpha > 0
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0")
    )
    notes: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    pick_log: Mapped[SystemPickLog] = relationship(back_populates="outcomes")


class TradePlan(Base):
    """사용자가 매일 입력하는 매매 plan — Integrated v10 추천 + 사용자 달러 금액.

    Phase A (advisory): 시스템은 picks + entry/stop/1R/2R 제공, 사용자가 종목별 amount_usd 입력.
    실제 주문은 사용자 수동 (Webull 앱). 시스템은 입력값으로 paper PnL 추적.
    """

    __tablename__ = "trade_plans"
    __table_args__ = (
        UniqueConstraint("plan_date", "symbol", name="uq_trade_plan_date_sym"),
        Index("ix_trade_plan_date", "plan_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-3 추천 순위

    # 사용자 입력
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # 시스템 산출 (입력 시점 snapshot)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    stop_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_1r: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    target_2r: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    composite_score: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"))
    score_meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    sector: Mapped[str | None] = mapped_column(String(48))

    # 산출 (입력 시점 자동 계산)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # 2-Tier 자동매매 — broker가 발송한 주문 ID 영속 추적 (재발송 멱등성)
    broker_order_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)

    # Intraday confirmation (Phase 5, 09:45 ET) — 0009 migration
    orb_high: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    orb_low: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    session_vwap: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    intraday_rvol: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    premarket_gap_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    premarket_rvol: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    # 'watchlist' (preopen 산출) | 'passed' | 'failed' | 'sent' | 'skipped'
    confirm_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="watchlist", server_default="watchlist"
    )
    # 'user_fixed': 사용자가 /trading에서 직접 입력한 plan — run_trade(09:30)가 입력값 그대로 발송.
    # 'orb_auto':  스캐너 preopen 자동 watchlist — run_confirm(09:45)가 ORB로 재계산 후 발송.
    dispatch_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="orb_auto", server_default="orb_auto"
    )

    # 2.2 부분 청산 진행도 (0011 migration) — monitor가 broker_order_ids[0/1] children 조회 후 동기화.
    # broker_order_ids[0] = 1차 bracket (target_1r leg) → filled_qty_1r / filled_avg_price_1r
    # broker_order_ids[1] = 2차 bracket (target_2r leg) → filled_qty_2r / filled_avg_price_2r
    filled_qty_1r: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filled_qty_2r: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filled_avg_price_1r: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    filled_avg_price_2r: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)

    # 1.1 entry partial fill 추적 (0012 migration) — BUY parent 자체의 filled_qty.
    # expected_holding = (entry_filled_qty_1 + entry_filled_qty_2) - (filled_qty_1r + filled_qty_2r)
    entry_filled_qty_1: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_filled_qty_2: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    outcomes: Mapped[list[TradePlanOutcome]] = relationship(
        back_populates="trade_plan", cascade="all, delete-orphan"
    )


class TradePlanOutcome(Base):
    """trade_plans의 1d/5d/10d 실현 수익 + 사용자 입력 $ 기반 손익."""

    __tablename__ = "trade_plan_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "trade_plan_id", "horizon_days", name="uq_trade_outcome_plan_horizon"
        ),
        Index("ix_trade_outcome_horizon_date", "horizon_days", "exit_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_plan_id: Mapped[int] = mapped_column(
        ForeignKey("trade_plans.id", ondelete="CASCADE"), index=True
    )
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 5, 10
    exit_date: Mapped[date] = mapped_column(Date, nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    pct_return: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    spy_pct_return: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    alpha: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0")
    )
    hit_target_1r: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_target_2r: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    qty_sold_at_1r: Mapped[int] = mapped_column(Integer, default=0)
    qty_sold_at_2r: Mapped[int] = mapped_column(Integer, default=0)
    partial_realized_pnl_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0")
    )
    notes: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    trade_plan: Mapped[TradePlan] = relationship(back_populates="outcomes")


class AdvisorRecommendation(Base):
    """AI 자문 에이전트 추천 — Claude Opus 4.7이 산출.

    morning  : 09:25 preopen 직전, 그 날 진입 후보 batch
    intraday_entry / intraday_add / intraday_exit : 장중 제안
    사용자 Telegram 승인을 거쳐야 trade_plan으로 변환되어 발주된다.
    expires_at 경과 시 status='expired' 자동 전환 (절대 자동 실행 X).
    """

    __tablename__ = "advisor_recommendations"
    __table_args__ = (
        UniqueConstraint(
            "rec_date", "symbol", "rec_type",
            name="uq_advisor_rec_date_symbol_type",
        ),
        Index("ix_advisor_rec_date_status", "rec_date", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rec_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 'morning' | 'intraday_entry' | 'intraday_add' | 'intraday_exit'
    rec_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), default="BUY", server_default="BUY")

    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    target_1r: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    target_2r: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    qty: Mapped[int | None] = mapped_column(Integer)

    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    reasoning_text: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    context_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    # pending | approved | rejected | expired | executed
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    user_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trade_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("trade_plans.id", ondelete="SET NULL"), nullable=True
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StrategyAssignment(Base):
    """종목별 활성 전략 (또는 프리셋) 토글.

    - preset_key가 set이면 등록된 PRESETS 중 하나 (예: "bnf_style", "cis_style")
    - preset_key="custom" + params(JSONB)로 사용자 정의 시그널 조합 저장
    - enabled=False는 "히스토리 보존하되 비활성" 상태
    """

    __tablename__ = "strategy_assignments"
    __table_args__ = (
        Index("ix_assignments_symbol_preset", "symbol", "preset_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    preset_key: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(default=True)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(String(500))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
