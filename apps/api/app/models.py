from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Keep timestamps UTC when reading from SQLite.

    SQLite drops tzinfo on bind, so values read back would be naive and the
    frontend would treat them as local time. Re-attach UTC on read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def new_id() -> str:
    return uuid.uuid4().hex


class CronJob(Base):
    """One scheduled prompt bound to an Agent and a stable SDK session id.

    The session id is a transcript identity, not a ``sessions`` row: scheduled
    turns resume the same Claude SDK transcript so they share conversation
    context. The id is generated when the job is created and regenerated on the
    next trigger when its transcript no longer exists.
    """

    __tablename__ = "cron_jobs"
    __table_args__ = (
        UniqueConstraint("agent_id", "name", name="uq_cron_job_agent_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str] = mapped_column(Text)
    schedule: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_skipped_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    push_channel_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    push_conversation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ChannelInbound(Base):
    """Idempotency record for an inbound WeChat message id.

    Channel configuration lives in ``settings.json``, so ``channel_id`` is a
    plain settings id rather than a foreign key. ``session_id`` names an SDK
    transcript, not a ``sessions`` row.
    """

    __tablename__ = "channel_inbounds"
    __table_args__ = (
        UniqueConstraint("channel_id", "external_message_id", name="uq_channel_inbound"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(32), index=True)
    external_message_id: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="")
    external_id: Mapped[str] = mapped_column(String(200), default="", index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChannelDelivery(Base):
    __tablename__ = "channel_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(200), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Backtest(Base):
    """One user-submitted crypto backtest and its bounded result payload."""

    __tablename__ = "backtests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    granularity: Mapped[str] = mapped_column(String(10))
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    initial_capital: Mapped[float] = mapped_column(Float, default=10_000.0)
    fee_rate: Mapped[float] = mapped_column(Float, default=0.001)
    slippage_rate: Mapped[float] = mapped_column(Float, default=0.0005)
    strategy_code: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[dict] = mapped_column("progress_json", JSON, default=dict)

    metrics: Mapped[dict | None] = mapped_column("metrics_json", JSON, nullable=True)
    equity: Mapped[list | None] = mapped_column("equity_json", JSON, nullable=True)
    drawdown: Mapped[list | None] = mapped_column("drawdown_json", JSON, nullable=True)
    candles: Mapped[list | None] = mapped_column("candles_json", JSON, nullable=True)
    trades: Mapped[list | None] = mapped_column("trades_json", JSON, nullable=True)
    warnings: Mapped[list] = mapped_column("warnings_json", JSON, default=list)
    data_start_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    data_end_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bar_count: Mapped[int] = mapped_column(Integer, default=0)
    trades_total: Mapped[int] = mapped_column(Integer, default=0)
    trades_truncated: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RunningSession(Base):
    """Runtime lease marking a session with an active turn.

    Startup recovery consumes this table to find sessions that were mid-run.
    """

    __tablename__ = "running_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
