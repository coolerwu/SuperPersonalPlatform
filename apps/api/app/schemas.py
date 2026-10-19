from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = None
    provider_id: str | None = None
    status: str | None = None


class AgentOut(BaseModel):
    id: str
    name: str
    instructions: str
    provider_id: str
    provider_name: str = ""
    model: str = ""
    status: str
    created_at: datetime
    updated_at: datetime


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
