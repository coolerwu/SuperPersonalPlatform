from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.skills import normalize_skill_names
from app.services.timeframes import validate_granularity

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,30}$")


class LoginRequest(BaseModel):
    token: str = Field(min_length=1)


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    base_url: str
    model: str
    has_api_key: bool
    created_at: datetime
    updated_at: datetime


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = ""


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = ""
    provider_id: str | None = None
    auto_memory: bool = True
    enabled_skills: list[str] = Field(default_factory=list)

    @field_validator("enabled_skills")
    @classmethod
    def _validate_enabled_skills(cls, value: list[str]) -> list[str]:
        return normalize_skill_names(value)


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = None
    provider_id: str | None = None
    status: str | None = None
    auto_memory: bool | None = None
    enabled_skills: list[str] | None = None

    @field_validator("enabled_skills")
    @classmethod
    def _validate_enabled_skills(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_skill_names(value)


class AgentOut(BaseModel):
    id: str
    name: str
    instructions: str
    provider_id: str
    provider_name: str = ""
    model: str = ""
    status: str
    auto_memory: bool = True
    enabled_skills: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SkillOut(BaseModel):
    name: str
    description: str
    path: str
    directory: str
    include_tools: list[str] = Field(default_factory=list)
    can_enable: bool = True
    warnings: list[str] = Field(default_factory=list)
    source: str = "manual"


class SkillCatalogOut(BaseModel):
    skills: list[SkillOut] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SkillDetailOut(BaseModel):
    name: str
    directory: str
    path: str
    content: str
    warnings: list[str] = Field(default_factory=list)
    source: str = "manual"


class SkillUpdate(BaseModel):
    content: str = Field(min_length=1)


class ToolOut(BaseModel):
    name: str
    description: str = ""
    registered: bool = False
    skills: list[str] = Field(default_factory=list)


class ToolsOut(BaseModel):
    platform_tools: list[ToolOut] = Field(default_factory=list)
    filesystem_tools: list[ToolOut] = Field(default_factory=list)
    skill_tools: list[ToolOut] = Field(default_factory=list)


class SessionCreate(BaseModel):
    title: str | None = None


class SessionOut(BaseModel):
    id: str
    agent_id: str
    title: str
    origin: str = "chat"
    message_count: int = 0
    active_run_id: str | None = None
    active_run_status: str | None = None
    channel_type: str | None = None
    channel_name: str | None = None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime


class CronJobCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=32)
    name: str = ""
    prompt: str = ""
    schedule: str = ""
    timezone: str = ""
    enabled: bool = True
    push_channel_id: str = ""
    push_conversation_id: str = ""


class CronJobUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    schedule: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    push_channel_id: str | None = None
    push_conversation_id: str | None = None


class CronJobOut(BaseModel):
    id: str
    agent_id: str
    name: str
    prompt: str
    schedule: str
    timezone: str
    enabled: bool
    session_id: str | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_skipped_at: datetime | None = None
    push_channel_id: str | None = None
    push_conversation_id: str | None = None
    push_channel_name: str = ""
    push_conversation_name: str = ""
    created_at: datetime
    updated_at: datetime


class ChannelCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = Field(default=None, pattern="^(active|paused)$")


class ChannelOut(BaseModel):
    id: str
    agent_id: str
    type: str
    name: str
    status: str
    account_id: str = ""
    has_credentials: bool = False
    conversation_count: int = 0
    last_seen_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ChannelConversationOut(BaseModel):
    id: str
    channel_id: str
    display_name: str
    session_id: str
    session_title: str = ""
    created_at: datetime
    updated_at: datetime


class ChannelLoginOut(BaseModel):
    qrcode: str
    scan_url: str
    qr_svg: str
    expires_in: int


class ChannelLoginStatusOut(BaseModel):
    status: str
    account_id: str = ""


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    run_id: str | None
    created_at: datetime


class MessagePageOut(BaseModel):
    items: list[MessageOut]
    has_more: bool
    next_before: str | None = None


class MessageSearchHitOut(BaseModel):
    message_id: str
    session_id: str
    session_title: str
    role: str
    snippet: str
    created_at: datetime


class ChatRequest(BaseModel):
    session_id: str
    content: str = Field(min_length=1)
    client_message_id: str | None = None
    agent_id: str = "default"


class ChatResponse(BaseModel):
    run_id: str
    session_id: str
    message_id: str


class TurnAcceptedOut(BaseModel):
    session_id: str
    turn_id: str
    accepted: bool = True


class SessionSummaryOut(BaseModel):
    id: str
    title: str
    last_modified: int = 0
    channel_type: str | None = None
    channel_name: str | None = None
    busy: bool = False


class TranscriptMessageOut(BaseModel):
    id: str
    role: str
    content: str = ""
    blocks: list[dict] = []


class ChatCommandRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    session_id: str | None = None
    # /memory takes an argument and user memory has no length cap.
    command: str = Field(min_length=2)


class ChatCommandResponse(BaseModel):
    command: str
    reply: str
    cancelled_count: int = 0


class AttachmentOut(BaseModel):
    name: str
    path: str
    size: int


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    session_id: str
    source: str
    status: str
    input: str
    output: str | None
    error: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    type: str
    payload: dict
    created_at: datetime


class MemoryCreate(BaseModel):
    scope: str = Field(pattern="^(user|agent)$")
    agent_id: str | None = None
    content: str = Field(min_length=1)
    tags: list[str] = []


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    agent_id: str | None
    content: str
    tags: list[str]
    source_message_id: str | None
    source_run_id: str | None
    source_session_id: str | None
    source_kind: str
    snapshot_text: str | None
    snapshot_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    # Filled by the list endpoint only; None means "not checked".
    source_message_exists: bool | None = None
    source_session_exists: bool | None = None
    source_agent_id: str | None = None


class BacktestCreate(BaseModel):
    symbol: str = Field(min_length=3, max_length=30)
    granularity: str = Field(min_length=2, max_length=10)
    start_ms: int = Field(gt=0)
    end_ms: int = Field(gt=0)
    initial_capital: float = Field(default=10_000.0, gt=0, le=1e12)
    fee_rate: float = Field(default=0.001, ge=0, le=0.1)
    slippage_rate: float = Field(default=0.0005, ge=0, le=0.1)
    warmup_bars: int = Field(default=200, ge=0, le=5_000)
    strategy_code: str = Field(min_length=1, max_length=200_000)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not _SYMBOL_RE.match(symbol):
            raise ValueError("交易对只能包含大写字母和数字，例如 BTCUSDT")
        return symbol

    @field_validator("granularity")
    @classmethod
    def _validate_granularity(cls, value: str) -> str:
        return validate_granularity(value)

    @model_validator(mode="after")
    def _validate_range(self) -> "BacktestCreate":
        if self.end_ms <= self.start_ms:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class BacktestSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    symbol: str
    granularity: str
    start_ms: int
    end_ms: int
    initial_capital: float
    fee_rate: float
    slippage_rate: float
    status: str
    error: str | None
    metrics: dict | None = None
    warnings: list = Field(default_factory=list)
    bar_count: int = 0
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class BacktestDetailOut(BacktestSummaryOut):
    strategy_code: str
    cancel_requested: bool = False
    error_detail: str | None = None
    progress: dict = Field(default_factory=dict)
    data_start_ms: int | None = None
    data_end_ms: int | None = None
    trades_total: int = 0
    trades_truncated: bool = False


class BacktestResultOut(BaseModel):
    id: str
    status: str
    metrics: dict | None = None
    equity: list = Field(default_factory=list)
    drawdown: list = Field(default_factory=list)
    candles: list = Field(default_factory=list)
    trades: list = Field(default_factory=list)
    warnings: list = Field(default_factory=list)
    trades_total: int = 0
    trades_truncated: bool = False
    data_start_ms: int | None = None
    data_end_ms: int | None = None
    bar_count: int = 0


class BacktestGranularityOut(BaseModel):
    value: str
    label: str


class BacktestMetaOut(BaseModel):
    granularities: list[BacktestGranularityOut] = Field(default_factory=list)
    max_bars: int
    max_trades: int
    worker_running: bool = False
    proxy_configured: bool = False
    bitget_base_url: str


class MarketStatusOut(BaseModel):
    symbol: str
    granularity: str
    rows: int = 0
    first_ms: int | None = None
    last_ms: int | None = None
    oldest_available_ms: int | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None


class MarketProbeOut(BaseModel):
    ok: bool
    latency_ms: int | None = None
    error: str | None = None
    base_url: str
    proxy_configured: bool = False


class KnowledgeConfigUpdate(BaseModel):
    enabled: bool | None = None
    webdav_url: str | None = Field(default=None, max_length=500)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=500)
    clear_password: bool = False
    root: str | None = Field(default=None, max_length=500)
    interval_seconds: int | None = Field(default=None, ge=30, le=86400)


class AppSettingsUpdate(BaseModel):
    session_retention_days: int = Field(ge=0, le=3650)


class TradeJournalCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    entry_at: str = Field(min_length=1, max_length=64)
    entry_price: float = Field(gt=0)
    thesis: str = Field(min_length=1, max_length=20000)
    direction: str = "long"
    market: str | None = None
    currency: str | None = None
    instrument_type: str | None = None
    stop_loss: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    fees: float = Field(default=0.0, ge=0)
    exit_at: str | None = None
    exit_price: float | None = Field(default=None, gt=0)
    review: str = ""
    tags: list[str] | None = None
    followed_plan: bool | None = None
    meta: dict | None = None


class TradeJournalUpdate(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    entry_at: str | None = Field(default=None, min_length=1, max_length=64)
    entry_price: float | None = Field(default=None, gt=0)
    thesis: str | None = Field(default=None, min_length=1, max_length=20000)
    direction: str | None = None
    market: str | None = None
    currency: str | None = None
    instrument_type: str | None = None
    stop_loss: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    fees: float | None = Field(default=None, ge=0)
    exit_at: str | None = None
    exit_price: float | None = Field(default=None, gt=0)
    review: str | None = None
    tags: list[str] | None = None
    followed_plan: bool | None = None
    meta: dict | None = None


class TradeJournalClose(BaseModel):
    exit_at: str = Field(min_length=1, max_length=64)
    exit_price: float = Field(gt=0)
    fees: float | None = Field(default=None, ge=0)
    review: str | None = None
    tags: list[str] | None = None
    followed_plan: bool | None = None


class ChanAnalyzeRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=30)
    granularity: str = Field(default="1h", min_length=2, max_length=10)
    bars: int = Field(default=500, ge=20, le=1000)
    start_date: str | None = Field(default=None, max_length=40)
    end_date: str | None = Field(default=None, max_length=40)


class ChanBacktestRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=30)
    granularity: str = Field(default="1h", min_length=2, max_length=10)
    bars: int = Field(default=500, ge=60, le=1000)
    start_date: str | None = Field(default=None, max_length=40)
    end_date: str | None = Field(default=None, max_length=40)
    initial_capital: float = Field(default=10_000.0, gt=0, le=1e12)
    fee_rate: float = Field(default=0.001, ge=0, le=0.1)
    slippage_rate: float = Field(default=0.0005, ge=0, le=0.1)
    stop_pct: float = Field(default=0.01, gt=0, le=0.2)
    risk_pct: float = Field(default=0.01, gt=0, le=0.1)
    r_multiple: float = Field(default=2.0, gt=0, le=50)
    max_hold_bars: int = Field(default=50, ge=1, le=1000)


class AllocationMarkInput(BaseModel):
    price: float = Field(gt=0)


class AllocationAccrualInput(BaseModel):
    annual_rate: float = Field(ge=0, le=100)
    start: str = Field(min_length=1, max_length=20)
    compound: bool = False


class AllocationPortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    currency: str = Field(default="USDT", min_length=2, max_length=10)
    cash_target_pct: float | None = Field(default=None, ge=0, le=100)
    rebalance_band_pct: float = Field(default=5.0, ge=0, le=50)
    targets: dict[str, float] = Field(default_factory=dict)
    marks: dict[str, AllocationMarkInput] = Field(default_factory=dict)
    accruals: dict[str, AllocationAccrualInput] = Field(default_factory=dict)
    note: str = Field(default="", max_length=20_000)


class AllocationPortfolioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    currency: str | None = Field(default=None, min_length=2, max_length=10)
    cash_target_pct: float | None = Field(default=None, ge=0, le=100)
    rebalance_band_pct: float | None = Field(default=None, ge=0, le=50)
    targets: dict[str, float] | None = None
    marks: dict[str, AllocationMarkInput] | None = None
    accruals: dict[str, AllocationAccrualInput] | None = None
    note: str | None = Field(default=None, max_length=20_000)


class AllocationEntryCreate(BaseModel):
    type: str = Field(min_length=1, max_length=20)
    at: str = Field(min_length=1, max_length=64)
    amount: float | None = Field(default=None, gt=0)
    symbol: str | None = Field(default=None, max_length=32)
    quantity: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    fees: float | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=2_000)


class AllocationEntryUpdate(BaseModel):
    type: str | None = Field(default=None, min_length=1, max_length=20)
    at: str | None = Field(default=None, min_length=1, max_length=64)
    amount: float | None = Field(default=None, gt=0)
    symbol: str | None = Field(default=None, max_length=32)
    quantity: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    fees: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=2_000)
