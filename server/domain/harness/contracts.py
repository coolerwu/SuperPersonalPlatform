from dataclasses import dataclass
from enum import StrEnum
from typing import Awaitable, Callable, Protocol

from server.domain.agents import AgentDefinition, ModelDefinition


class AgentChatUnavailableError(Exception):
    pass


class AgentToolCallingUnsupportedError(AgentChatUnavailableError):
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


class AgentModelGateway(Protocol):
    async def complete(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...],
    ) -> str: ...

    async def reason_with_tools(
        self,
        model: ModelDefinition,
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
        model: ModelDefinition,
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
    llm_client: AgentModelGateway


class HarnessMode(StrEnum):
    PROMPT = "prompt"
    TOOLS = "tools"


@dataclass(frozen=True)
class HarnessRequest:
    mode: HarnessMode
    content: str
    images: tuple[ChatImage, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_registry: AgentToolDispatcher | None = None
    tool_runtime: object | None = None
