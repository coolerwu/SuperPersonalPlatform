from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from server.domain.agents import AgentDefinition, ModelDefinition


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


CheckpointCallback = Callable[[AgentChatCheckpoint], Awaitable[None]]
CheckpointEmitter = Callable[[str, str, str], Awaitable[None]]


@dataclass(frozen=True)
class ChatOptions:
    on_checkpoint: CheckpointCallback | None = None
    max_iterations: int = 60


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
    content: str
    images: tuple[ChatImage, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_registry: AgentToolDispatcher | None = None
    tool_runtime: object | None = None


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
        agent: Agent,
        request: HarnessRequest,
        options: ChatOptions,
        emit: CheckpointEmitter,
    ) -> str: ...
