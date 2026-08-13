from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class SectorTag(str, enum.Enum):
    ai = "ai"
    defence = "defence"
    nuclear = "nuclear"
    green_energy = "green_energy"
    quantum = "quantum"
    robotics = "robotics"
    space = "space"


class Decision(str, enum.Enum):
    trade = "trade"
    watchlist = "watchlist"
    no_trade = "no_trade"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Security(SQLModel, table=True):
    __tablename__ = "security"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(
        sa_column=Column(sa.String(length=16), nullable=False, unique=True, index=True)
    )
    name: str = Field(sa_column=Column(sa.String(length=255), nullable=False))
    exchange: str = Field(sa_column=Column(sa.String(length=64), nullable=False))
    sector_tag: SectorTag = Field(
        sa_column=Column(
            sa.String(length=32),
            sa.CheckConstraint(
                "sector_tag IN ('ai','defence','nuclear','green_energy','quantum','robotics','space')",
            ),
            nullable=False,
        )
    )
    currency: str = Field(
        default="USD", sa_column=Column(sa.String(length=8), nullable=False)
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, default=_utc_now),
    )


class PriceBar(SQLModel, table=True):
    __tablename__ = "price_bar"
    __table_args__ = (
        sa.Index("ix_price_bar_security_date_desc", "security_id", sa.desc("bar_date")),
    )

    security_id: int = Field(foreign_key="security.id", primary_key=True)
    bar_date: date = Field(sa_column=Column(sa.Date, primary_key=True, nullable=False))
    open: Decimal = Field(sa_column=Column(sa.Numeric(14, 4), nullable=False))
    high: Decimal = Field(sa_column=Column(sa.Numeric(14, 4), nullable=False))
    low: Decimal = Field(sa_column=Column(sa.Numeric(14, 4), nullable=False))
    close: Decimal = Field(sa_column=Column(sa.Numeric(14, 4), nullable=False))
    volume: int = Field(sa_column=Column(sa.BigInteger, nullable=False))


class Fundamental(SQLModel, table=True):
    __tablename__ = "fundamental"

    id: Optional[int] = Field(default=None, primary_key=True)
    security_id: int = Field(foreign_key="security.id", nullable=False)
    as_of_date: date = Field(sa_column=Column(sa.Date, nullable=False))
    revenue_growth: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(12, 6), nullable=True))
    fcf: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(18, 4), nullable=True))
    debt_to_equity: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(12, 6), nullable=True))
    eps_trend: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(12, 6), nullable=True))
    margins: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(12, 6), nullable=True))
    raw_payload: Dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, default=_utc_now),
    )


class EarningsEvent(SQLModel, table=True):
    __tablename__ = "earnings_event"

    security_id: int = Field(foreign_key="security.id", primary_key=True)
    report_date: date = Field(sa_column=Column(sa.Date, primary_key=True, nullable=False))
    confirmed: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
    )


class MacroDaily(SQLModel, table=True):
    __tablename__ = "macro_daily"

    obs_date: date = Field(sa_column=Column(sa.Date, primary_key=True, nullable=False))
    vix: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(8, 3), nullable=True))


class TechnicalFeature(SQLModel, table=True):
    __tablename__ = "technical_feature"

    security_id: int = Field(foreign_key="security.id", primary_key=True)
    as_of_date: date = Field(sa_column=Column(sa.Date, primary_key=True, nullable=False))
    rsi_14: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(7, 4), nullable=True))
    sma_50: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(14, 4), nullable=True))
    sma_200: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(14, 4), nullable=True))
    volume_trend: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(14, 6), nullable=True))


class SourceType(str, enum.Enum):
    filing_business = "filing_business"
    filing_risk = "filing_risk"
    earnings_call = "earnings_call"
    company_news = "company_news"
    sector_news = "sector_news"


class DocumentChunk(SQLModel, table=True):
    __tablename__ = "document_chunk"
    __table_args__ = (
        sa.Index("ix_document_chunk_ticker_source_type", "ticker", "source_type"),
        sa.Index("ix_document_chunk_published_at", "published_at"),
        sa.UniqueConstraint("source_hash", name="uq_document_chunk_source_hash"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(sa_column=Column(sa.String(length=16), nullable=False, index=True))
    source_type: SourceType = Field(
        sa_column=Column(
            sa.String(length=32),
            nullable=False,
        )
    )
    source_url: str = Field(sa_column=Column(sa.Text, nullable=False))
    source_hash: str = Field(sa_column=Column(sa.String(length=64), nullable=False))
    published_at: Optional[datetime] = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    ingested_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, default=_utc_now),
    )
    chunk_index: int = Field(sa_column=Column(sa.Integer, nullable=False))
    text: str = Field(sa_column=Column(sa.Text, nullable=False))
    embedding: Optional[List[float]] = Field(
        default=None,
        sa_column=Column(Vector(768), nullable=True),
    )


class Recommendation(SQLModel, table=True):
    __tablename__ = "recommendation"
    __table_args__ = (
        sa.Index("ix_recommendation_created_at", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    security_id: int = Field(foreign_key="security.id", nullable=False)
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, default=_utc_now),
    )
    decision: Decision = Field(
        sa_column=Column(
            sa.String(length=16),
            sa.CheckConstraint(
                "decision IN ('trade','watchlist','no_trade')",
            ),
            nullable=False,
        )
    )
    entry_low: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(14, 4), nullable=True))
    entry_high: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(14, 4), nullable=True))
    stop_loss: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(14, 4), nullable=True))
    take_profit_1: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(14, 4), nullable=True))
    take_profit_2: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(14, 4), nullable=True))
    risk_reward: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(10, 4), nullable=True))
    position_size: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(10, 4), nullable=True))
    confidence: Optional[Decimal] = Field(sa_column=Column(sa.Numeric(4, 3), nullable=True))
    reasons: List[str] = Field(sa_column=Column(JSONB, nullable=False), default_factory=list)
    warnings: List[str] = Field(sa_column=Column(JSONB, nullable=False), default_factory=list)
    full_payload: Dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    engine_version: str = Field(sa_column=Column(sa.String(length=64), nullable=False))


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"

    run_id: str = Field(sa_column=Column(sa.String(length=36), primary_key=True))
    ticker: str = Field(sa_column=Column(sa.String(length=16), nullable=False, index=True))
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(sa.DateTime(timezone=True), nullable=False, default=_utc_now),
    )
    final_decision: Optional[Decision] = Field(
        default=None, sa_column=Column(sa.String(length=16), nullable=True)
    )
    total_latency_ms: float = Field(default=0.0, nullable=False)
    token_usage: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    warnings: List[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))


class AgentRunStep(SQLModel, table=True):
    __tablename__ = "agent_run_steps"
    __table_args__ = (sa.UniqueConstraint("run_id", "sequence", name="uq_agent_run_steps_order"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="agent_runs.run_id", nullable=False, index=True)
    sequence: int = Field(nullable=False)
    agent_name: str = Field(sa_column=Column(sa.String(length=64), nullable=False))
    status: str = Field(sa_column=Column(sa.String(length=16), nullable=False))
    tool_calls: List[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    latency_ms: float = Field(default=0.0, nullable=False)
    token_usage: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    retries: int = Field(default=0, nullable=False)
    output: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB, nullable=True))
