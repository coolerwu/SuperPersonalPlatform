from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from server.domain.agents import AgentDefinition, HarnessMode, ModelDefinition


class AgentChatUnavailableError(Exception):
    pass


class AgentToolCallingUnsupportedError(AgentChatUnavailableError):
    pass


class AgentRunFailedError(RuntimeError):
    pass


class AgentRunBlockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatImage:
    mime_type: str
    data: str


@dataclass(frozen=True)
class AgentToolCall:
    id: str
    name: str
    args: dict[str, object]


@dataclass(frozen=True)
class AgentToolResult:
    tool_call_id: str
    content: str


@dataclass(frozen=True)
class AgentToolReasoningResult:
    content: str
    tool_calls: tuple[AgentToolCall, ...]
    messages: tuple[object, ...]


@dataclass(frozen=True)
class AgentChatCheckpoint:
    stage: str
    title: str
    detail: str = ""


@dataclass(frozen=True)
class AgentRunArtifactEvent:
    field: str
    payload: object


CheckpointCallback = Callable[[AgentChatCheckpoint], Awaitable[None]]
CheckpointEmitter = Callable[[str, str, str], Awaitable[None]]
AgentRunArtifactCallback = Callable[[AgentRunArtifactEvent], Awaitable[None]]


class AgentModelRunner(Protocol):
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...],
    ) -> str: ...

    async def reason_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tool_names: tuple[str, ...],
        messages: tuple[object, ...],
        images: tuple[ChatImage, ...],
    ) -> AgentToolReasoningResult: ...

    def append_tool_results(
        self,
        messages: tuple[object, ...],
        results: tuple[AgentToolResult, ...],
    ) -> tuple[object, ...]: ...

    async def force_tool_final(
        self,
        messages: tuple[object, ...],
    ) -> str: ...


class AgentToolDispatcher(Protocol):
    async def dispatch(
        self,
        name: str,
        args: dict[str, object],
        runtime: object | None,
    ) -> str: ...


@dataclass(frozen=True)
class Agent:
    definition: AgentDefinition
    model: ModelDefinition


@dataclass(frozen=True)
class HarnessRequest:
    agent: Agent
    content: str
    images: tuple[ChatImage, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_registry: AgentToolDispatcher | None = None
    tool_runtime: object | None = None
    on_checkpoint: CheckpointCallback | None = None
    on_run_artifact: AgentRunArtifactCallback | None = None
    max_iterations: int = 60

    @classmethod
    def for_prompt(
        cls,
        *,
        agent: Agent,
        content: str,
        images: tuple[ChatImage, ...] = (),
        on_checkpoint: CheckpointCallback | None = None,
        on_run_artifact: AgentRunArtifactCallback | None = None,
    ) -> "HarnessRequest":
        if agent.model.mode is not HarnessMode.PROMPT:
            raise ValueError("Prompt 请求必须绑定 Prompt 模式模型")
        normalized_content = content.strip()
        if not normalized_content and not images:
            raise ValueError("消息内容不能为空")
        return cls(
            agent=agent,
            content=normalized_content,
            images=images,
            on_checkpoint=on_checkpoint,
            on_run_artifact=on_run_artifact,
        )

    @classmethod
    def for_agent(
        cls,
        *,
        agent: Agent,
        content: str,
        images: tuple[ChatImage, ...] = (),
        tool_names: tuple[str, ...] = (),
        tool_registry: AgentToolDispatcher | None = None,
        tool_runtime: object | None = None,
        on_checkpoint: CheckpointCallback | None = None,
        on_run_artifact: AgentRunArtifactCallback | None = None,
        max_iterations: int = 60,
    ) -> "HarnessRequest":
        if agent.model.mode is not HarnessMode.AGENT:
            raise ValueError("Agent 请求必须绑定 Agent 模式模型")
        normalized_content = content.strip()
        if not normalized_content and not images:
            raise ValueError("消息内容不能为空")
        if max_iterations <= 0:
            raise ValueError("max_iterations must be greater than zero")
        if tool_names and tool_registry is None:
            raise ValueError("tool_registry is required when tool_names are provided")
        if not tool_names and (tool_registry is not None or tool_runtime is not None):
            raise ValueError("tool context requires tool_names")
        return cls(
            agent=agent,
            content=normalized_content,
            images=images,
            tool_names=tool_names,
            tool_registry=tool_registry,
            tool_runtime=tool_runtime,
            on_checkpoint=on_checkpoint,
            on_run_artifact=on_run_artifact,
            max_iterations=max_iterations,
        )


@dataclass(frozen=True)
class GoalContract:
    goal: str
    completion_criteria: tuple[str, ...]
    output_format: str
    required_evidence: tuple[str, ...]


@dataclass(frozen=True)
class RawToolResult:
    tool_call_id: str
    tool_name: str
    content: str
    ok: bool


@dataclass(frozen=True)
class EvidenceRecord:
    source: str
    content: str
    ok: bool


@dataclass(frozen=True)
class OutputCandidate:
    content: str


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    blocked: bool
    feedback: str


class AgentVerifier(Protocol):
    async def verify(
        self,
        agent: Agent,
        goal: GoalContract,
        evidence: tuple[EvidenceRecord, ...],
        candidate: OutputCandidate,
    ) -> VerificationResult: ...


class HarnessModeRunner(Protocol):
    async def run(
        self,
        request: HarnessRequest,
        emit: CheckpointEmitter,
    ) -> str: ...
