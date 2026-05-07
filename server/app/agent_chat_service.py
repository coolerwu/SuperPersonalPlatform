from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, TypedDict

import yaml

from server.domain.agents import (
    AgentConfigError,
    AgentDefinition,
    AgentPlatformDefinition,
    ModelDefinition,
)
from server.app.agent_skill_service import AgentSkillService, AgentSkillToolbox
from server.infrastructure.config import load_settings, parse_settings


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


class AgentChatModelGateway(Protocol):
    async def complete(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...] = (),
    ) -> str:
        pass

    async def complete_with_tools(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        tool_names: tuple[str, ...],
        skill_tools: AgentSkillToolbox,
        images: tuple[ChatImage, ...] = (),
        max_iterations: int = 60,
    ) -> str:
        pass

    async def reason_with_tools(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        tool_names: tuple[str, ...],
        messages: tuple[Any, ...],
        images: tuple[ChatImage, ...] = (),
    ) -> AgentToolReasoningResult:
        pass

    def append_tool_results(
        self,
        messages: tuple[Any, ...],
        tool_results: tuple[AgentToolResult, ...],
    ) -> tuple[Any, ...]:
        pass

    async def force_tool_final(
        self,
        model: ModelDefinition,
        messages: tuple[Any, ...],
    ) -> str:
        pass


@dataclass(frozen=True)
class BoundModelOption:
    id: str
    name: str
    model: str
    base_url: str
    supports_images: bool
    has_api_key: bool


@dataclass(frozen=True)
class AgentOption:
    id: str
    name: str
    model_id: str | None
    model: BoundModelOption | None


@dataclass(frozen=True)
class AgentOptions:
    default_agent_id: str
    agents: tuple[AgentOption, ...]


@dataclass(frozen=True)
class EditableModel:
    id: str
    name: str
    base_url: str
    model: str
    temperature: float | None
    supports_images: bool
    has_api_key: bool
    api_key_mask: str


@dataclass(frozen=True)
class EditableAgent:
    id: str
    name: str
    model_id: str | None
    system_prompt: str
    skill_ids: tuple[str, ...]


@dataclass(frozen=True)
class AgentConfigSnapshot:
    path: str
    default_model_id: str
    default_agent_id: str
    common_skill_tools: tuple[str, ...]
    models: tuple[EditableModel, ...]
    agents: tuple[EditableAgent, ...]


class AgentGraphState(TypedDict):
    system_prompt: str
    user_message: str
    images: tuple[ChatImage, ...]
    model: ModelDefinition
    common_skill_tools: tuple[str, ...]
    task_goal: str
    tool_messages: tuple[Any, ...]
    pending_tool_calls: tuple[AgentToolCall, ...]
    tool_iterations: int
    assistant_message: str


TASK_GOAL_CONFIRMATION_PROMPT = (
    "请从用户输入中提炼本次单次 task 的明确目标，只返回一句简洁目标；不要回答任务本身。"
)


class AgentChatService:
    def __init__(
        self,
        config_path: str | Path,
        model_gateway: AgentChatModelGateway,
    ) -> None:
        self._config_path = Path(config_path)
        self._model_gateway = model_gateway
        self._skill_service = AgentSkillService(self._config_path.parent)

    def options(self) -> AgentOptions:
        platform = self._load_platform()
        return AgentOptions(
            default_agent_id=platform.default_agent_id,
            agents=tuple(
                AgentOption(
                    id=agent.id,
                    name=agent.name,
                    model_id=agent.model_id,
                    model=self._bound_model_option(platform, agent),
                )
                for agent in platform.agents
            ),
        )

    def config_snapshot(self) -> AgentConfigSnapshot:
        platform = self._load_platform()
        return AgentConfigSnapshot(
            path=str(self._config_path),
            default_model_id=platform.default_model_id,
            default_agent_id=platform.default_agent_id,
            common_skill_tools=platform.common_skill_tools,
            models=tuple(
                EditableModel(
                    id=model.id,
                    name=model.name,
                    base_url=model.base_url,
                    model=model.model,
                    temperature=model.temperature,
                    supports_images=model.supports_images,
                    has_api_key=self._has_usable_api_key(model),
                    api_key_mask="********" if self._has_usable_api_key(model) else "",
                )
                for model in platform.models
            ),
            agents=tuple(
                EditableAgent(
                    id=agent.id,
                    name=agent.name,
                    model_id=agent.model_id,
                    system_prompt=agent.system_prompt,
                    skill_ids=agent.skill_ids,
                )
                for agent in platform.agents
            ),
        )

    def update_config(self, payload: dict[str, Any]) -> None:
        raw = self._read_raw_config()
        old_settings = parse_settings(raw)
        models = payload.get("models") or []
        agents = payload.get("agents") or []
        common_skill_tools = payload.get("common_skill_tools")
        if not isinstance(models, list):
            raise AgentConfigError("models must be a list")
        if not isinstance(agents, list):
            raise AgentConfigError("agents must be a list")
        if common_skill_tools is None:
            common_skill_tools = raw.get("common_skills", {}).get("tools", [])
        if not isinstance(common_skill_tools, list):
            raise AgentConfigError("common_skill_tools must be a list")

        old_keys = {model.id: model.api_key for model in old_settings.agent_platform.models}
        old_agent_skill_ids = {
            agent.id: agent.skill_ids for agent in old_settings.agent_platform.agents
        }
        normalized_models: list[dict[str, Any]] = []
        for model in models:
            if not isinstance(model, dict):
                raise AgentConfigError("models[] must be an object")
            model_id = str(model.get("id") or "").strip()
            api_key = str(model.get("api_key") or "").strip()
            normalized_models.append(
                {
                    "id": model_id,
                    "name": str(model.get("name") or "").strip(),
                    "base_url": str(model.get("base_url") or "").strip(),
                    "api_key": api_key or old_keys.get(model_id, ""),
                    "model": str(model.get("model") or "").strip(),
                    "temperature": self._optional_float(model.get("temperature")),
                    "supports_images": bool(model.get("supports_images", False)),
                }
            )

        normalized_agents: list[dict[str, Any]] = []
        for agent in agents:
            if not isinstance(agent, dict):
                raise AgentConfigError("agents[] must be an object")
            model_id = agent.get("model_id")
            raw_skill_ids = agent.get("skill_ids")
            if raw_skill_ids is None:
                skill_ids = old_agent_skill_ids.get(str(agent.get("id") or "").strip(), ())
            else:
                skill_ids = raw_skill_ids
            if not isinstance(skill_ids, (list, tuple)):
                raise AgentConfigError("agents[].skill_ids must be a list")
            normalized_agents.append(
                {
                    "id": str(agent.get("id") or "").strip(),
                    "name": str(agent.get("name") or "").strip(),
                    "model_id": str(model_id).strip() if model_id is not None else "",
                    "system_prompt": str(agent.get("system_prompt") or "").strip(),
                    "skill_ids": [str(skill_id).strip() for skill_id in skill_ids if str(skill_id).strip()],
                }
            )

        raw.pop("permissions", None)
        raw["llm"] = {
            "default_model_id": str(payload.get("default_model_id") or "").strip(),
            "models": normalized_models,
        }
        raw["agents"] = {
            "default_agent_id": str(payload.get("default_agent_id") or "").strip(),
            "definitions": normalized_agents,
        }
        raw["common_skills"] = {
            "tools": [str(tool).strip() for tool in common_skill_tools if str(tool).strip()]
        }
        parse_settings(raw)
        self._write_raw_config(raw)

    async def chat(
        self,
        agent_id: str,
        content: str,
        images: tuple[ChatImage, ...] = (),
        on_checkpoint: Callable[[AgentChatCheckpoint], Awaitable[None]] | None = None,
    ) -> str:
        platform = self._load_platform()
        if not platform.agents:
            raise AgentChatUnavailableError("未配置 Agent")
        if not platform.models:
            raise AgentChatUnavailableError("未配置模型")
        if not content.strip() and not images:
            raise AgentConfigError("消息内容不能为空")

        agent = platform.get_agent(agent_id or platform.default_agent_id)
        if not agent.model_id:
            raise AgentChatUnavailableError("Agent 未配置模型")
        model = platform.get_model(agent.model_id)
        if not self._has_usable_api_key(model):
            raise AgentChatUnavailableError("模型 API Key 不可用")
        if images and not model.supports_images:
            raise AgentChatUnavailableError("当前模型不支持图片输入")
        return await self._run_graph(
            agent,
            model,
            platform.common_skill_tools,
            content.strip(),
            images,
            on_checkpoint,
        )

    def _load_platform(self) -> AgentPlatformDefinition:
        return load_settings(self._config_path).agent_platform

    def _read_raw_config(self) -> dict[str, Any]:
        raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise AgentConfigError("config.yaml 顶层必须是对象")
        return raw

    def _write_raw_config(self, raw: dict[str, Any]) -> None:
        tmp_path = self._config_path.with_suffix(".yaml.tmp")
        tmp_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp_path.replace(self._config_path)

    def _bound_model_option(
        self,
        platform: AgentPlatformDefinition,
        agent: AgentDefinition,
    ) -> BoundModelOption | None:
        if not agent.model_id:
            return None
        try:
            model = platform.get_model(agent.model_id)
        except AgentConfigError:
            return None
        return BoundModelOption(
            id=model.id,
            name=model.name,
            model=model.model,
            base_url=model.base_url,
            supports_images=model.supports_images,
            has_api_key=self._has_usable_api_key(model),
        )

    def _has_usable_api_key(self, model: ModelDefinition) -> bool:
        return bool(model.api_key.strip()) and model.api_key.strip() != "change-me"

    def _optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value)

    async def _run_graph(
        self,
        agent: AgentDefinition,
        model: ModelDefinition,
        common_skill_tools: tuple[str, ...],
        content: str,
        images: tuple[ChatImage, ...],
        on_checkpoint: Callable[[AgentChatCheckpoint], Awaitable[None]] | None = None,
    ) -> str:
        async def emit(stage: str, title: str, detail: str = "") -> None:
            if on_checkpoint is not None:
                await on_checkpoint(AgentChatCheckpoint(stage=stage, title=title, detail=detail))

        async def confirm_task_goal(state: AgentGraphState) -> dict[str, str]:
            await emit("goal", "确认 task 目标")
            task_goal = await self._model_gateway.complete(
                state["model"],
                TASK_GOAL_CONFIRMATION_PROMPT,
                state["user_message"],
                state["images"],
            )
            task_goal = task_goal.strip()
            await emit("goal", "task 目标已确认", task_goal)
            return {"task_goal": task_goal}

        def skill_system_prompt(state: AgentGraphState) -> str:
            system_prompt = state["system_prompt"]
            if state["task_goal"]:
                system_prompt = f"{system_prompt}\n\n本次 task 目标：{state['task_goal']}"
            return system_prompt

        async def direct_model(state: AgentGraphState) -> dict[str, str]:
            await emit("answer", "生成最终回复")
            message = await self._model_gateway.complete(
                state["model"],
                skill_system_prompt(state),
                state["user_message"],
                state["images"],
            )
            await emit("answer", "最终回复已生成")
            return {"assistant_message": message}

        async def reason_skill_tools(state: AgentGraphState) -> dict[str, object]:
            await emit(
                "reason",
                "推理下一步",
                f"第 {state['tool_iterations'] + 1} 轮",
            )
            system_prompt = skill_system_prompt(state)
            if state["common_skill_tools"]:
                system_prompt = (
                    f"{system_prompt}\n\n"
                    "你可以在需要时通过只读工具 list_skill 和 read_skill 读取当前 Agent "
                    "显式绑定的技能说明。不要假装读取不存在或未绑定的 skill。"
                )
                result = await self._model_gateway.reason_with_tools(
                    state["model"],
                    system_prompt,
                    state["user_message"],
                    state["common_skill_tools"],
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
                    "tool_iterations": state["tool_iterations"] + 1,
                }
            return await direct_model(state)

        async def act_skill_tools(state: AgentGraphState) -> dict[str, object]:
            skill_tools = self._skill_service.toolbox(agent)
            tool_results: list[AgentToolResult] = []
            for tool_call in state["pending_tool_calls"]:
                await emit("act", f"执行工具 {tool_call.name}", self._tool_checkpoint_detail(tool_call))
                if tool_call.name == "list_skill":
                    content = await skill_tools.list_skill()
                elif tool_call.name == "read_skill":
                    content = await skill_tools.read_skill(str(tool_call.args.get("id") or ""))
                else:
                    content = f"Unsupported tool: {tool_call.name}"
                await emit("act", f"工具完成 {tool_call.name}")
                tool_results.append(
                    AgentToolResult(tool_call_id=tool_call.id, content=content)
                )
            return {
                "tool_messages": self._model_gateway.append_tool_results(
                    state["tool_messages"],
                    tuple(tool_results),
                ),
                "pending_tool_calls": (),
            }

        async def force_tool_final(state: AgentGraphState) -> dict[str, str]:
            await emit("answer", "达到 60 轮上限，生成最终回复")
            return {
                "assistant_message": await self._model_gateway.force_tool_final(
                    state["model"],
                    state["tool_messages"],
                )
            }

        def route_after_reason(state: AgentGraphState) -> str:
            if not state["pending_tool_calls"]:
                return "end"
            if state["tool_iterations"] >= 60:
                return "force_final"
            return "act"

        initial_state: AgentGraphState = {
            "system_prompt": agent.system_prompt,
            "user_message": content,
            "images": images,
            "model": model,
            "common_skill_tools": common_skill_tools,
            "task_goal": "",
            "tool_messages": (),
            "pending_tool_calls": (),
            "tool_iterations": 0,
            "assistant_message": "",
        }

        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            goal_result = await confirm_task_goal(initial_state)
            state: AgentGraphState = {**initial_state, **goal_result}
            if not state["common_skill_tools"]:
                result = await direct_model(state)
                return result["assistant_message"]
            while True:
                reason_result = await reason_skill_tools(state)
                state = {**state, **reason_result}
                route = route_after_reason(state)
                if route == "end":
                    return state["assistant_message"]
                if route == "force_final":
                    final_result = await force_tool_final(state)
                    return final_result["assistant_message"]
                act_result = await act_skill_tools(state)
                state = {**state, **act_result}

        graph = StateGraph(AgentGraphState)
        graph.add_node("confirm_task_goal", confirm_task_goal)
        graph.add_node("direct_model", direct_model)
        graph.add_node("reason_skill_tools", reason_skill_tools)
        graph.add_node("act_skill_tools", act_skill_tools)
        graph.add_node("force_tool_final", force_tool_final)
        graph.add_edge(START, "confirm_task_goal")
        graph.add_conditional_edges(
            "confirm_task_goal",
            lambda state: "reason" if state["common_skill_tools"] else "direct",
            {
                "reason": "reason_skill_tools",
                "direct": "direct_model",
            },
        )
        graph.add_conditional_edges(
            "reason_skill_tools",
            route_after_reason,
            {
                "act": "act_skill_tools",
                "force_final": "force_tool_final",
                "end": END,
            },
        )
        graph.add_edge("act_skill_tools", "reason_skill_tools")
        graph.add_edge("direct_model", END)
        graph.add_edge("force_tool_final", END)
        app = graph.compile()
        result = await app.ainvoke(initial_state)
        return str(result.get("assistant_message") or "")

    def _tool_checkpoint_detail(self, tool_call: AgentToolCall) -> str:
        if tool_call.name == "read_skill":
            return str(tool_call.args.get("id") or "")
        return ""
