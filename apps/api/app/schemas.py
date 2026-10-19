from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.skills import normalize_skill_names
from app.services.skill_tools import normalize_tool_names


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
    enabled_tools: list[str] = Field(default_factory=list)

    @field_validator("enabled_skills")
    @classmethod
    def _validate_enabled_skills(cls, value: list[str]) -> list[str]:
        return normalize_skill_names(value)

    @field_validator("enabled_tools")
    @classmethod
    def _validate_enabled_tools(cls, value: list[str]) -> list[str]:
        return normalize_tool_names(value)


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = None
    provider_id: str | None = None
    status: str | None = None
    auto_memory: bool | None = None
    enabled_skills: list[str] | None = None
    enabled_tools: list[str] | None = None

    @field_validator("enabled_skills")
    @classmethod
    def _validate_enabled_skills(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_skill_names(value)

    @field_validator("enabled_tools")
    @classmethod
    def _validate_enabled_tools(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_tool_names(value)


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
    enabled_tools: list[str] = Field(default_factory=list)
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


class SkillCatalogOut(BaseModel):
    skills: list[SkillOut] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ToolOut(BaseModel):
    name: str
    description: str = ""
    registered: bool = False
    skills: list[str] = Field(default_factory=list)


class ToolsOut(BaseModel):
    filesystem_tools: list[ToolOut] = Field(default_factory=list)
    skill_tools: list[ToolOut] = Field(default_factory=list)


class SessionCreate(BaseModel):
    title: str | None = None


class SessionOut(BaseModel):
    id: str
    agent_id: str
    title: str
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    run_id: str | None
    created_at: datetime


class ChatRequest(BaseModel):
    session_id: str
    content: str = Field(min_length=1)
    client_message_id: str | None = None


class ChatResponse(BaseModel):
    run_id: str
    session_id: str
    message_id: str


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
    content: str = Field(min_length=1, max_length=1000)
    tags: list[str] = []


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    tags: list[str] | None = None


class MemoryConsolidate(BaseModel):
    scope: str = Field(pattern="^(user|agent)$")
    agent_id: str


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    agent_id: str | None
    content: str
    tags: list[str]
    source_message_id: str | None
    source_run_id: str | None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
