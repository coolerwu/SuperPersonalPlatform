from dataclasses import dataclass, replace
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


class AgentRunPhase(StrEnum):
    REASONING = "reasoning"
    TOOL_RUNNING = "tool_running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"


@dataclass(frozen=True)
class HarnessRequest:
    mode: HarnessMode
    content: str
    images: tuple[ChatImage, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_registry: AgentToolDispatcher | None = None
    tool_runtime: object | None = None


@dataclass(frozen=True)
class AgentRunState:
    phase: AgentRunPhase
    turn: int = 0
    messages: tuple[object, ...] = ()
    pending_tool_calls: tuple[AgentToolCall, ...] = ()
    assistant_message: str = ""


async def run_agent(
    agent: Agent,
    request: HarnessRequest,
    options: ChatOptions | None = None,
) -> str:
    options = options or ChatOptions()
    _validate_request(request, options)

    async def emit(stage: str, title: str, detail: str = "") -> None:
        if options.on_checkpoint is not None:
            await options.on_checkpoint(
                AgentChatCheckpoint(stage=stage, title=title, detail=detail)
            )

    if request.mode is HarnessMode.PROMPT:
        await emit("answer", "生成最终回复")
        message = await agent.llm_client.complete(
            agent.model,
            agent.definition.system_prompt,
            request.content,
            request.images,
        )
        await emit("answer", "最终回复已生成")
        return message

    return await _run_tools(agent, request, options, emit)


async def _run_tools(
    agent: Agent,
    request: HarnessRequest,
    options: ChatOptions,
    emit: Callable[[str, str, str], Awaitable[None]],
) -> str:
    state = AgentRunState(phase=AgentRunPhase.REASONING)
    system_prompt = (
        f"{agent.definition.system_prompt}\n\n"
        "你可以在需要时调用平台暴露的 typed tools。Skills 是操作规程，"
        "tools 才是真正可调用能力；不要假装读取不存在或未绑定的 skill，"
        "也不要假装执行未暴露的 tool。"
    )

    while state.phase is not AgentRunPhase.COMPLETED:
        if state.phase is AgentRunPhase.REASONING:
            next_turn = state.turn + 1
            await emit("reason", "推理下一步", f"第 {next_turn} 轮")
            result = await agent.llm_client.reason_with_tools(
                agent.model,
                system_prompt,
                request.content,
                request.tool_names,
                state.messages,
                request.images,
            )
            if result.tool_calls:
                await emit(
                    "reason",
                    "模型请求工具",
                    ", ".join(tool_call.name for tool_call in result.tool_calls),
                )
                state = replace(
                    state,
                    phase=AgentRunPhase.TOOL_RUNNING,
                    turn=next_turn,
                    messages=result.messages,
                    pending_tool_calls=result.tool_calls,
                )
            elif result.content:
                await emit("answer", "最终回复已生成")
                state = replace(
                    state,
                    phase=AgentRunPhase.COMPLETED,
                    turn=next_turn,
                    messages=result.messages,
                    assistant_message=result.content,
                )
            else:
                state = replace(
                    state,
                    phase=(
                        AgentRunPhase.FINALIZING
                        if next_turn >= options.max_iterations
                        else AgentRunPhase.REASONING
                    ),
                    turn=next_turn,
                    messages=result.messages,
                )
            continue

        if state.phase is AgentRunPhase.TOOL_RUNNING:
            tool_results = []
            assert request.tool_registry is not None
            for tool_call in state.pending_tool_calls:
                await emit(
                    "act",
                    f"执行工具 {tool_call.name}",
                    _tool_checkpoint_detail(tool_call),
                )
                content = await request.tool_registry.dispatch(
                    tool_call.name,
                    tool_call.args,
                    request.tool_runtime,
                )
                await emit("act", f"工具完成 {tool_call.name}", "")
                tool_results.append(
                    AgentToolResult(tool_call_id=tool_call.id, content=content)
                )
            state = replace(
                state,
                phase=(
                    AgentRunPhase.FINALIZING
                    if state.turn >= options.max_iterations
                    else AgentRunPhase.REASONING
                ),
                messages=agent.llm_client.append_tool_results(
                    state.messages,
                    tuple(tool_results),
                ),
                pending_tool_calls=(),
            )
            continue

        if state.phase is AgentRunPhase.FINALIZING:
            await emit(
                "answer",
                f"达到 {options.max_iterations} 轮上限，生成最终回复",
                "",
            )
            message = await agent.llm_client.force_tool_final(
                agent.model,
                state.messages,
            )
            state = replace(
                state,
                phase=AgentRunPhase.COMPLETED,
                assistant_message=message,
            )

    return state.assistant_message


def _validate_request(request: HarnessRequest, options: ChatOptions) -> None:
    if options.max_iterations <= 0:
        raise ValueError("max_iterations must be greater than zero")
    if request.mode is HarnessMode.PROMPT:
        if request.tool_names or request.tool_registry is not None or request.tool_runtime is not None:
            raise ValueError("prompt mode does not accept tools")
        return
    if request.mode is HarnessMode.TOOLS:
        if not request.tool_names:
            raise ValueError("tools mode requires tool_names")
        if request.tool_registry is None:
            raise ValueError("tools mode requires tool_registry")
        return
    raise ValueError(f"unsupported harness mode: {request.mode}")


def _tool_checkpoint_detail(tool_call: AgentToolCall) -> str:
    if tool_call.name == "read_skill":
        return str(tool_call.args.get("id") or "")
    return ""
