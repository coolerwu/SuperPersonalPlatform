from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
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


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), default="default")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), default="Assistant")
    instructions: Mapped[str] = mapped_column(Text, default="")
    provider_id: Mapped[str] = mapped_column(String(32), ForeignKey("providers.id"), default="default")
    status: Mapped[str] = mapped_column(String(20), default="active")
    auto_memory: Mapped[bool] = mapped_column(Boolean, default=True)
    memory_turn_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    enabled_skills: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(String(32), ForeignKey("agents.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    origin: Mapped[str] = mapped_column(String(20), default="chat", server_default="chat")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


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
    agent_id: Mapped[str] = mapped_column(String(32), ForeignKey("agents.id"), index=True)
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


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(String(32), ForeignKey("agents.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("sessions.id"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    input: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id"), index=True)
    type: Mapped[str] = mapped_column(String(50), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(120), default="user")
    action: Mapped[str] = mapped_column(String(120))
    entity: Mapped[str] = mapped_column(String(120))
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MemoryItem(Base):
    __tablename__ = "memory_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(20), index=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("agents.id"), nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # explicit: legacy rows created by the old chat "记住…" command; manual:
    # added/edited in the memory page; command: written by the /memory command;
    # agent: written by the Agent itself through memory_save; legacy: rows that
    # predate provenance tracking.
    source_kind: Mapped[str] = mapped_column(String(20), default="manual")
    snapshot_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RunMemory(Base):
    __tablename__ = "run_memories"

    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id"), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("memory_items.id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Channel(Base):
    """One external messaging identity bound to one Agent.

    ``type`` is currently always ``wechat``; the iLink bot credentials are
    written after QR login and never returned to the web client.
    """

    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(String(32), ForeignKey("agents.id"), index=True)
    type: Mapped[str] = mapped_column(String(20), default="wechat")
    name: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="pairing", index=True)
    account_id: Mapped[str] = mapped_column(String(200), default="")
    bot_token: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    ilink_user_id: Mapped[str] = mapped_column(String(200), default="")
    sync_buf: Mapped[str] = mapped_column(Text, default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ChannelConversation(Base):
    """External WeChat contact <-> platform Session mapping (one per Session)."""

    __tablename__ = "channel_conversations"
    __table_args__ = (
        UniqueConstraint("channel_id", "external_id", name="uq_channel_conversation"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    channel_id: Mapped[str] = mapped_column(String(32), ForeignKey("channels.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(200), default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id"), unique=True, index=True
    )
    context_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_token_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ChannelInbound(Base):
    """Idempotency record for an inbound WeChat message id."""

    __tablename__ = "channel_inbounds"
    __table_args__ = (
        UniqueConstraint("channel_id", "external_message_id", name="uq_channel_inbound"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(32), ForeignKey("channels.id"), index=True)
    external_message_id: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="")
    external_id: Mapped[str] = mapped_column(String(200), default="", index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChannelRunLink(Base):
    """Marks a Run as channel-originated so its final answer is sent back.

    Web messages sent inside a channel Session do not get a link, so they are
    not pushed to WeChat. Retries of channel Runs copy the link.
    """

    __tablename__ = "channel_run_links"

    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id"), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(32), ForeignKey("channels.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("channel_conversations.id"), index=True
    )
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


class KnowledgeConfig(Base):
    """Singleton WebDAV knowledge-base settings managed from the config page.

    The password is stored in app.db like provider API keys: never returned by
    the API, never written to logs.
    """

    __tablename__ = "knowledge_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    webdav_url: Mapped[str] = mapped_column(String(500), default="")
    username: Mapped[str] = mapped_column(String(200), default="")
    password: Mapped[str] = mapped_column(Text, default="")
    root: Mapped[str] = mapped_column(String(500), default="")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AppConfig(Base):
    """Singleton platform settings managed from the config page."""

    __tablename__ = "app_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    session_retention_days: Mapped[int] = mapped_column(Integer, default=30)
    memory_consolidate_every_turns: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class RunningSession(Base):
    """Runtime lease marking a session with an active turn.

    Startup recovery consumes this table to find sessions that were mid-run.
    """

    __tablename__ = "running_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
