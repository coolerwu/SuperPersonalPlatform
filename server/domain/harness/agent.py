from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypedDict

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
    args: dict[str, Any]


@dataclass(frozen=True)
class AgentToolResult:
    tool_call_id: str
    content: str


@dataclass(frozen=True)
class AgentToolReasoningResult:
    content: str
    tool_calls: tuple[AgentToolCall, ...]
    messages: tuple[Any, ...]


@dataclass(frozen=True)
class AgentChatCheckpoint:
    stage: str
    title: str
    detail: str = ""


@dataclass(frozen=True)
class ChatOptions:
    on_checkpoint: Callable[[AgentChatCheckpoint], Awaitable[None]] | None = None
    max_iterations: int = 60


@dataclass(frozen=True)
class Agent:
    definition: AgentDefinition
    model: ModelDefinition
    llm_client: Any


@dataclass(frozen=True)
class PromptSkillContext:
    content: str
    images: tuple[ChatImage, ...] = ()


@dataclass(frozen=True)
class ReactSkillContext:
    content: str
    images: tuple[ChatImage, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_registry: Any = None
    tool_runtime: Any = None


SkillContext = PromptSkillContext | ReactSkillContext


class AgentGraphState(TypedDict):
    system_prompt: str
    user_message: str
    images: tuple[ChatImage, ...]
    model: ModelDefinition
    tool_names: tuple[str, ...]
    max_iterations: int
    tool_messages: tuple[Any, ...]
    pending_tool_calls: tuple[AgentToolCall, ...]
    turn: int
    tool_iterations: int
    assistant_message: str
    next_step: str


async def run_agent(
    agent: Agent,
    skill_context: SkillContext,
    options: ChatOptions | None = None,
) -> str:
    options = options or ChatOptions()

    async def emit(stage: str, title: str, detail: str = "") -> None:
        if options.on_checkpoint is not None:
            await options.on_checkpoint(AgentChatCheckpoint(stage=stage, title=title, detail=detail))

    if isinstance(skill_context, PromptSkillContext):
        await emit("answer", "生成最终回复")
        message = await agent.llm_client.complete(
            agent.model,
            agent.definition.system_prompt,
            skill_context.content,
            skill_context.images,
        )
        await emit("answer", "最终回复已生成")
        return message

    content = skill_context.content
    images = skill_context.images
    tool_names = skill_context.tool_names
    tool_registry = skill_context.tool_registry
    tool_runtime = skill_context.tool_runtime

    async def reason(state: AgentGraphState) -> dict[str, object]:
        await emit("reason", "推理下一步", f"第 {state['turn'] + 1} 轮")
        if state["tool_names"]:
            system_prompt = (
                f"{state['system_prompt']}\n\n"
                "你可以在需要时调用平台暴露的 typed tools。Skills 是操作规程，"
                "tools 才是真正可调用能力；不要假装读取不存在或未绑定的 skill，"
                "也不要假装执行未暴露的 tool。"
            )
            result = await agent.llm_client.reason_with_tools(
                state["model"],
                system_prompt,
                state["user_message"],
                state["tool_names"],
                state["tool_messages"],
                state["images"],
            )
            if result.tool_calls:
                await emit(
                    "reason",
                    "模型请求工具",
                    ", ".join(tool_call.name for tool_call in result.tool_calls),
                )
            else:
                await emit("answer", "最终回复已生成")
            return {
                "assistant_message": result.content if not result.tool_calls else "",
                "pending_tool_calls": result.tool_calls,
                "tool_messages": result.messages,
            }
        await emit("answer", "生成最终回复")
        message = await agent.llm_client.complete(
            state["model"],
            state["system_prompt"],
            state["user_message"],
            state["images"],
        )
        await emit("answer", "最终回复已生成")
        return {"assistant_message": message, "pending_tool_calls": ()}

    async def act(state: AgentGraphState) -> dict[str, object]:
        tool_results: list[AgentToolResult] = []
        for tool_call in state["pending_tool_calls"]:
            await emit("act", f"执行工具 {tool_call.name}", _tool_checkpoint_detail(tool_call))
            content = await tool_registry.dispatch(
                tool_call.name,
                tool_call.args,
                tool_runtime,
            )
            await emit("act", f"工具完成 {tool_call.name}")
            tool_results.append(AgentToolResult(tool_call_id=tool_call.id, content=content))
        return {
            "tool_messages": agent.llm_client.append_tool_results(
                state["tool_messages"],
                tuple(tool_results),
            ),
            "pending_tool_calls": (),
            "tool_iterations": state["tool_iterations"] + 1,
        }

    async def check(state: AgentGraphState) -> dict[str, object]:
        turn = state["turn"] + 1
        next_step = (
            "reason"
            if not state["assistant_message"] and turn < state["max_iterations"]
            else "finalize"
        )
        return {"turn": turn, "next_step": next_step}

    async def finalize(state: AgentGraphState) -> dict[str, str]:
        if state["assistant_message"]:
            return {}
        await emit("answer", f"达到 {state['max_iterations']} 轮上限，生成最终回复")
        return {
                "assistant_message": await agent.llm_client.force_tool_final(
                    state["model"],
                state["tool_messages"],
            )
        }

    initial_state: AgentGraphState = {
        "system_prompt": agent.definition.system_prompt,
        "user_message": content,
        "images": images,
        "model": agent.model,
        "tool_names": tool_names,
        "max_iterations": options.max_iterations,
        "tool_messages": (),
        "pending_tool_calls": (),
        "turn": 0,
        "tool_iterations": 0,
        "assistant_message": "",
        "next_step": "reason",
    }

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        state: AgentGraphState = initial_state
        while True:
            reason_result = await reason(state)
            state = {**state, **reason_result}
            if state["pending_tool_calls"]:
                act_result = await act(state)
                state = {**state, **act_result}
            check_result = await check(state)
            state = {**state, **check_result}
            if state["next_step"] == "finalize":
                final_result = await finalize(state)
                state = {**state, **final_result}
                return str(state.get("assistant_message") or "")

    graph = StateGraph(AgentGraphState)
    graph.add_node("reason", reason)
    graph.add_node("act", act)
    graph.add_node("check", check)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "reason")
    graph.add_conditional_edges(
        "reason",
        lambda state: "act" if state["pending_tool_calls"] else "check",
        {"act": "act", "check": "check"},
    )
    graph.add_edge("act", "check")
    graph.add_conditional_edges(
        "check",
        lambda state: state["next_step"],
        {"reason": "reason", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    app = graph.compile()
    result = await app.ainvoke(initial_state)
    return str(result.get("assistant_message") or "")


def _tool_checkpoint_detail(tool_call: AgentToolCall) -> str:
    if tool_call.name == "read_skill":
        return str(tool_call.args.get("id") or "")
    return ""
