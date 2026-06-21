from dataclasses import dataclass, replace
from enum import StrEnum

from server.domain.harness.contracts import (
    Agent,
    AgentToolCall,
    AgentToolResult,
    ChatOptions,
    CheckpointEmitter,
    HarnessRequest,
)


class AgentRunPhase(StrEnum):
    REASONING = "reasoning"
    TOOL_RUNNING = "tool_running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"


@dataclass(frozen=True)
class AgentRunState:
    phase: AgentRunPhase
    turn: int = 0
    messages: tuple[object, ...] = ()
    pending_tool_calls: tuple[AgentToolCall, ...] = ()
    assistant_message: str = ""


async def run_tools_mode(
    agent: Agent,
    request: HarnessRequest,
    options: ChatOptions,
    emit: CheckpointEmitter,
) -> str:
    _validate_tools_request(request)
    state = AgentRunState(phase=AgentRunPhase.REASONING)
    system_prompt = (
        f"{agent.definition.system_prompt}\n\n"
        "你可以在需要时调用平台暴露的 typed tools。Skills 是操作规程，"
        "tools 才是真正可调用能力；不要假装读取不存在或未绑定的 skill，"
        "也不要假装执行未暴露的 tool。"
    )

    while state.phase is not AgentRunPhase.COMPLETED:
        if state.phase is AgentRunPhase.REASONING:
            state = await _reason(agent, request, options, state, system_prompt, emit)
            continue
        if state.phase is AgentRunPhase.TOOL_RUNNING:
            state = await _run_pending_tools(agent, request, options, state, emit)
            continue
        if state.phase is AgentRunPhase.FINALIZING:
            state = await _finalize(agent, options, state, emit)

    return state.assistant_message


async def _reason(
    agent: Agent,
    request: HarnessRequest,
    options: ChatOptions,
    state: AgentRunState,
    system_prompt: str,
    emit: CheckpointEmitter,
) -> AgentRunState:
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
        return replace(
            state,
            phase=AgentRunPhase.TOOL_RUNNING,
            turn=next_turn,
            messages=result.messages,
            pending_tool_calls=result.tool_calls,
        )
    if result.content:
        await emit("answer", "最终回复已生成", "")
        return replace(
            state,
            phase=AgentRunPhase.COMPLETED,
            turn=next_turn,
            messages=result.messages,
            assistant_message=result.content,
        )
    return replace(
        state,
        phase=(
            AgentRunPhase.FINALIZING
            if next_turn >= options.max_iterations
            else AgentRunPhase.REASONING
        ),
        turn=next_turn,
        messages=result.messages,
    )


async def _run_pending_tools(
    agent: Agent,
    request: HarnessRequest,
    options: ChatOptions,
    state: AgentRunState,
    emit: CheckpointEmitter,
) -> AgentRunState:
    tool_registry = request.tool_registry
    if tool_registry is None:
        raise ValueError("tools mode requires tool_registry")

    tool_results = []
    for tool_call in state.pending_tool_calls:
        await emit(
            "act",
            f"执行工具 {tool_call.name}",
            _tool_checkpoint_detail(tool_call),
        )
        content = await tool_registry.dispatch(
            tool_call.name,
            tool_call.args,
            request.tool_runtime,
        )
        await emit("act", f"工具完成 {tool_call.name}", "")
        tool_results.append(
            AgentToolResult(tool_call_id=tool_call.id, content=content)
        )
    return replace(
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


async def _finalize(
    agent: Agent,
    options: ChatOptions,
    state: AgentRunState,
    emit: CheckpointEmitter,
) -> AgentRunState:
    await emit(
        "answer",
        f"达到 {options.max_iterations} 轮上限，生成最终回复",
        "",
    )
    message = await agent.llm_client.force_tool_final(agent.model, state.messages)
    return replace(
        state,
        phase=AgentRunPhase.COMPLETED,
        assistant_message=message,
    )


def _validate_tools_request(request: HarnessRequest) -> None:
    if not request.tool_names:
        raise ValueError("tools mode requires tool_names")
    if request.tool_registry is None:
        raise ValueError("tools mode requires tool_registry")


def _tool_checkpoint_detail(tool_call: AgentToolCall) -> str:
    if tool_call.name == "read_skill":
        return str(tool_call.args.get("id") or "")
    return ""
