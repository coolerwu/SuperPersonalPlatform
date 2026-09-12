"""Group chat contracts, independent of the execution framework."""
from typing import Literal, NotRequired, TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator


class GroupMember(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(default="", max_length=20000)


class GroupDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    members: list[GroupMember] = Field(min_length=1, max_length=20)
    host_member_id: str
    archived: bool = False

    @model_validator(mode="after")
    def validate_members(self):
        ids = [member.id for member in self.members]
        if len(ids) != len(set(ids)) or self.host_member_id not in ids:
            raise ValueError("成员 ID 必须唯一，主持必须是群成员")
        names = [member.name for member in self.members]
        if len(names) != len(set(names)) or any("@" in name or "\n" in name for name in names):
            raise ValueError("群内名称不能重复或包含 @、换行")
        return self


class GroupTask(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    member_id: str
    task: str = Field(min_length=1, max_length=20000)


class GroupDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["dispatch", "finish"]
    tasks: list[GroupTask] = Field(default_factory=list, max_length=4)
    summary: str = Field(default="", max_length=30000)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "dispatch" and not self.tasks:
            raise ValueError("分工必须包含任务")
        if self.action == "finish" and (self.tasks or not self.summary.strip()):
            raise ValueError("结束必须提供总结且不能再分配任务")
        if len({task.member_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("同轮不能重复调度同一成员")
        return self


class GroupContextMessage(BaseModel):
    id: str
    content: str


class GroupRunContext(BaseModel):
    """Internal only: never accepted from the public Run API."""
    run_id: str = Field(pattern=r"^run_group_[a-f0-9]{32}_[0-9]+$")
    agent_snapshot: dict
    model_snapshot: dict = Field(default_factory=dict)
    messages: list[GroupContextMessage] = Field(default_factory=list)
    control_members: list[str] = Field(default_factory=list)
    finish_only: bool = False


# Persisted application records. Framework-specific objects never enter these documents.


class GroupMessage(TypedDict):
    id: str
    seq: int
    role: str
    content: str
    speaker: str
    member_id: str
    run_id: str
    created_at: str
    mentions: NotRequired[list[str]]
    thinking: NotRequired[list[str]]
    usage: NotRequired[dict | None]
    failed: NotRequired[bool]


class GroupStep(TypedDict):
    member_id: str
    task: str
    control: bool
    run_id: str
    session_id: str
    key: str
    content: str
    snapshot: dict
    messages: list[dict[str, str]]
    agent_id: str
    name: str


class GroupExecution(TypedDict):
    id: str
    client_message_id: str
    automatic: bool
    status: Literal["running", "waiting_approval", "paused", "stopping", "completed", "cancelled"]
    round: int
    goal: str
    snapshots: dict[str, dict]
    models: dict[str, dict]
    members: list[dict]
    host: str
    pending: list[dict]
    steps: list[GroupStep]
    current_step: GroupStep | None
    error: str


class ChatGroupState(TypedDict):
    id: str
    name: str
    members: list[dict]
    host_member_id: str
    archived: bool
    created_at: str
    updated_at: str
    messages: list[GroupMessage]
    executions: list[GroupExecution]
    member_sessions: dict[str, str]
    cursors: dict[str, int]
    active_run: NotRequired[dict | None]
