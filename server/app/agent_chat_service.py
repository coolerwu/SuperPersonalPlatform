from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypedDict

import yaml

from server.domain.agents import (
    AgentConfigError,
    AgentDefinition,
    AgentPlatformDefinition,
    ModelDefinition,
)
from server.infrastructure.config import load_settings, parse_settings


class AgentChatUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class ChatImage:
    mime_type: str
    data: str


class AgentChatModelGateway(Protocol):
    async def complete(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...] = (),
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


@dataclass(frozen=True)
class AgentConfigSnapshot:
    path: str
    default_model_id: str
    default_agent_id: str
    models: tuple[EditableModel, ...]
    agents: tuple[EditableAgent, ...]


class AgentGraphState(TypedDict):
    system_prompt: str
    user_message: str
    images: tuple[ChatImage, ...]
    model: ModelDefinition
    assistant_message: str


class AgentChatService:
    def __init__(
        self,
        config_path: str | Path,
        model_gateway: AgentChatModelGateway,
    ) -> None:
        self._config_path = Path(config_path)
        self._model_gateway = model_gateway

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
                )
                for agent in platform.agents
            ),
        )

    def update_config(self, payload: dict[str, Any]) -> None:
        raw = self._read_raw_config()
        old_settings = parse_settings(raw)
        models = payload.get("models") or []
        agents = payload.get("agents") or []
        if not isinstance(models, list):
            raise AgentConfigError("models must be a list")
        if not isinstance(agents, list):
            raise AgentConfigError("agents must be a list")

        old_keys = {model.id: model.api_key for model in old_settings.agent_platform.models}
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
            normalized_agents.append(
                {
                    "id": str(agent.get("id") or "").strip(),
                    "name": str(agent.get("name") or "").strip(),
                    "model_id": str(model_id).strip() if model_id is not None else "",
                    "system_prompt": str(agent.get("system_prompt") or "").strip(),
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
        parse_settings(raw)
        self._write_raw_config(raw)

    async def chat(
        self,
        agent_id: str,
        content: str,
        images: tuple[ChatImage, ...] = (),
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
        return await self._run_graph(agent, model, content.strip(), images)

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
        content: str,
        images: tuple[ChatImage, ...],
    ) -> str:
        async def call_model(state: AgentGraphState) -> dict[str, str]:
            message = await self._model_gateway.complete(
                state["model"],
                state["system_prompt"],
                state["user_message"],
                state["images"],
            )
            return {"assistant_message": message}

        initial_state: AgentGraphState = {
            "system_prompt": agent.system_prompt,
            "user_message": content,
            "images": images,
            "model": model,
            "assistant_message": "",
        }

        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            result = await call_model(initial_state)
            return result["assistant_message"]

        graph = StateGraph(AgentGraphState)
        graph.add_node("model", call_model)
        graph.add_edge(START, "model")
        graph.add_edge("model", END)
        app = graph.compile()
        result = await app.ainvoke(initial_state)
        return str(result.get("assistant_message") or "")
