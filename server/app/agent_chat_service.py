from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from server.domain.agents import (
    AgentConfigError,
    AgentDefinition,
    AgentPlatformDefinition,
    HarnessMode,
    ModelDefinition,
    SkillDefinition,
    ToolAccessDefinition,
)
from server.domain.harness import (
    Agent,
    AgentChatCheckpoint,
    AgentChatUnavailableError,
    ChatImage,
    HarnessRequest,
    run_agent,
)
from server.app.agent_skill_service import AgentSkillService
from server.app.agent_tool_service import (
    AgentToolRegistry,
    AgentToolRuntime,
    DEFAULT_AGENT_TOOL_REGISTRY,
)
from server.app.portfolio_service import PortfolioService
from server.infrastructure.config import load_settings, parse_settings


@dataclass(frozen=True)
class BoundModelOption:
    id: str
    name: str
    model: str
    base_url: str
    mode: HarnessMode
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
    agents: tuple[AgentOption, ...]
    portfolio_agent_id: str = ""


@dataclass(frozen=True)
class EditableModel:
    id: str
    name: str
    base_url: str
    model: str
    provider: str
    temperature: float | None
    supports_images: bool
    mode: HarnessMode
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
class EditableSkill:
    id: str
    name: str
    tools_allow: tuple[str, ...]


@dataclass(frozen=True)
class AgentConfigSnapshot:
    path: str
    default_model_id: str
    skills: tuple[EditableSkill, ...]
    models: tuple[EditableModel, ...]
    agents: tuple[EditableAgent, ...]
    portfolio_agent_id: str = ""


class AgentChatService:
    def __init__(
        self,
        config_path: str | Path,
        tool_registry: AgentToolRegistry | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        self._skill_service = AgentSkillService(self._config_path.parent)
        self._tool_registry = tool_registry or DEFAULT_AGENT_TOOL_REGISTRY

    def options(self) -> AgentOptions:
        platform = self._load_platform()
        agents = tuple(
            AgentOption(
                id=agent.id,
                name=agent.name,
                model_id=agent.model_id,
                model=self._bound_model_option(platform, agent),
            )
            for agent in platform.agents
        )
        return AgentOptions(
            agents=agents,
            portfolio_agent_id=load_settings(self._config_path).portfolio.agent_id,
        )

    def tool_definitions(self) -> tuple[dict[str, object], ...]:
        return self._tool_registry.public_definitions()

    def config_snapshot(self) -> AgentConfigSnapshot:
        platform = self._load_platform()

        agents = tuple(
            EditableAgent(
                id=agent.id,
                name=agent.name,
                model_id=agent.model_id,
                system_prompt=agent.system_prompt,
                skill_ids=agent.skill_ids,
            )
            for agent in platform.agents
        )

        return AgentConfigSnapshot(
            path=str(self._config_path),
            default_model_id=platform.default_model_id,
            skills=tuple(
                EditableSkill(
                    id=skill.id,
                    name=skill.name,
                    tools_allow=skill.tools.allow,
                )
                for skill in platform.skill_definitions
            ),
            models=tuple(
                EditableModel(
                    id=model.id,
                    name=model.name,
                    base_url=model.base_url,
                    model=model.model,
                    provider=model.provider,
                    temperature=model.temperature,
                    supports_images=model.supports_images,
                    mode=model.mode,
                    has_api_key=self._has_usable_api_key(model),
                    api_key_mask="********" if self._has_usable_api_key(model) else "",
                )
                for model in platform.models
            ),
            agents=agents,
            portfolio_agent_id=load_settings(self._config_path).portfolio.agent_id,
        )

    def update_config(self, payload: dict[str, Any]) -> None:
        raw = self._read_raw_config()
        old_settings = parse_settings(raw)
        models = payload.get("models") or []
        agents = payload.get("agents") or []
        skills = payload.get("skills")
        if not isinstance(models, list):
            raise AgentConfigError("models must be a list")
        if not isinstance(agents, list):
            raise AgentConfigError("agents must be a list")
        if skills is None:
            skills = raw.get("skills", {}).get("definitions", [])
        if not isinstance(skills, list):
            raise AgentConfigError("skills must be a list")
        if "common_skill_tools" in payload or "tools" in payload:
            raise AgentConfigError("legacy tool configuration is not supported")

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
                    "provider": str(model.get("provider") or "openai_compatible").strip() or "openai_compatible",
                    "temperature": self._optional_float(model.get("temperature")),
                    "supports_images": bool(model.get("supports_images", False)),
                    "mode": str(model.get("mode") or HarnessMode.PROMPT.value).strip(),
                }
            )

        normalized_skills: list[dict[str, Any]] = []
        for skill in skills:
            if not isinstance(skill, dict):
                raise AgentConfigError("skills[] must be an object")
            skill_id = str(skill.get("id") or "").strip()
            normalized_skills.append(
                {
                    "id": skill_id,
                }
            )

        normalized_agents: list[dict[str, Any]] = []
        for agent in agents:
            if not isinstance(agent, dict):
                raise AgentConfigError("agents[] must be an object")
            agent_id = str(agent.get("id") or "").strip()
            model_id = agent.get("model_id")
            raw_skill_ids = agent.get("skill_ids")

            if raw_skill_ids is None:
                skill_ids = old_agent_skill_ids.get(agent_id, ())
            else:
                skill_ids = raw_skill_ids
            if not isinstance(skill_ids, (list, tuple)):
                raise AgentConfigError("agents[].skill_ids must be a list")
            normalized_agents.append(
                {
                    "id": agent_id,
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
        raw.setdefault("agents", {})
        raw["skills"] = {"definitions": normalized_skills}
        raw["agents"].pop("default_agent_id", None)
        raw["agents"].pop("builtin_overrides", None)
        raw["agents"]["definitions"] = normalized_agents
        portfolio_agent_id = payload.get("portfolio_agent_id")
        if portfolio_agent_id is None:
            portfolio_agent_id = old_settings.portfolio.agent_id
        raw["portfolio"] = {"agent_id": str(portfolio_agent_id or "").strip()}
        raw.pop("common_skills", None)
        raw.pop("tools", None)
        parse_settings(raw)
        self._write_raw_config(raw)

    def read_skill_content(self, skill_id: str, agent_id: str | None = None) -> dict[str, object]:
        skill = self._skill_service.read_workspace_skill(skill_id, agent_id)
        return {
            "id": skill.id,
            "name": skill.name,
            "content": skill.content,
            "tools": {
                "allow": list(skill.tools.allow),
            },
            "truncated": skill.truncated,
        }

    def write_skill_content(
        self,
        skill_id: str,
        content: str,
        agent_id: str | None = None,
        *,
        name: str = "",
        tools: dict[str, object] | None = None,
    ) -> dict[str, object]:
        tool_access = self._tool_access_from_payload(tools or {})
        self._tool_registry.validate_tool_names(tool_access.allow)
        path = self._skill_service.write_workspace_skill(
            skill_id,
            content,
            agent_id,
            name=name,
            tools=tool_access,
        )
        return {"ok": True, "path": str(path)}

    async def chat(
        self,
        agent_id: str,
        content: str,
        images: tuple[ChatImage, ...] = (),
        on_checkpoint: Callable[[AgentChatCheckpoint], Awaitable[None]] | None = None,
    ) -> str:
        agent_id = agent_id.strip()
        if not agent_id:
            raise AgentConfigError("agent_id is required")
        if not content.strip() and not images:
            raise AgentConfigError("消息内容不能为空")
        platform = self._load_platform()
        agent = platform.get_agent(agent_id)
        model = self._model_for_agent(platform, agent)
        self._validate_bound_model(platform, model, images)
        bound_agent = Agent(definition=agent, model=model)
        if model.mode is HarnessMode.AGENT:
            tool_names = self._tool_registry.resolve_tools(
                agent,
                platform.skill_definitions,
            )
            request = HarnessRequest.for_agent(
                agent=bound_agent,
                content=content,
                images=images,
                tool_names=tool_names,
                tool_registry=self._tool_registry if tool_names else None,
                tool_runtime=self._tool_runtime(agent, tool_names) if tool_names else None,
                on_checkpoint=on_checkpoint,
            )
        else:
            request = HarnessRequest.for_prompt(
                agent=bound_agent,
                content=content,
                images=images,
                on_checkpoint=on_checkpoint,
            )
        return await run_agent(request)

    def bind_prompt_agent(
        self,
        *,
        agent_id: str,
        name: str,
        system_prompt: str,
        model_id: str | None = None,
    ) -> Agent:
        platform = self._load_platform()
        resolved_model_id = (model_id or platform.default_model_id).strip()
        if not resolved_model_id:
            raise AgentConfigError("未配置默认模型")
        model = platform.get_model(resolved_model_id)
        if model.mode is not HarnessMode.PROMPT:
            raise AgentConfigError("临时 Prompt Agent 必须绑定 Prompt 模式模型")
        if not self._has_usable_api_key(model):
            raise AgentChatUnavailableError("模型 API Key 不可用")
        definition = AgentDefinition(
            id=agent_id.strip(),
            name=name.strip(),
            system_prompt=system_prompt.strip(),
            model_id=model.id,
        )
        return Agent(definition=definition, model=model)

    async def run_with_tool_runtime(
        self,
        agent_id: str,
        content: str,
        tool_runtime: AgentToolRuntime,
        on_checkpoint: Callable[[AgentChatCheckpoint], Awaitable[None]] | None = None,
    ) -> str:
        agent_id = agent_id.strip()
        if not agent_id:
            raise AgentConfigError("agent_id is required")
        if not content.strip():
            raise AgentConfigError("消息内容不能为空")
        platform = self._load_platform()
        agent = platform.get_agent(agent_id)
        model = self._model_for_agent(platform, agent)
        self._validate_bound_model(platform, model)
        bound_agent = Agent(definition=agent, model=model)
        if model.mode is HarnessMode.AGENT:
            tool_names = self._tool_registry.resolve_tools(
                agent,
                platform.skill_definitions,
            )
            request = HarnessRequest.for_agent(
                agent=bound_agent,
                content=content,
                tool_names=tool_names,
                tool_registry=self._tool_registry if tool_names else None,
                tool_runtime=tool_runtime if tool_names else None,
                on_checkpoint=on_checkpoint,
            )
        else:
            request = HarnessRequest.for_prompt(
                agent=bound_agent,
                content=content,
                on_checkpoint=on_checkpoint,
            )
        return await run_agent(request)

    def _validate_bound_model(
        self,
        platform: AgentPlatformDefinition,
        model: ModelDefinition,
        images: tuple[ChatImage, ...] = (),
    ) -> None:
        if not platform.agents:
            raise AgentChatUnavailableError("未配置 Agent")
        if not platform.models:
            raise AgentChatUnavailableError("未配置模型")
        if not self._has_usable_api_key(model):
            raise AgentChatUnavailableError("模型 API Key 不可用")
        if images and not model.supports_images:
            raise AgentChatUnavailableError("当前模型不支持图片输入")

    def _tool_runtime(
        self,
        agent: AgentDefinition,
        tool_names: tuple[str, ...],
    ) -> AgentToolRuntime:
        portfolio_tool_names = {
            "list_portfolio_holdings",
            "add_portfolio_holding",
            "update_portfolio_holding",
            "delete_portfolio_holding",
        }
        if any(name in portfolio_tool_names for name in tool_names):
            return AgentToolRuntime(
                skill_tools=self._skill_service.toolbox(agent),
                portfolio_service=PortfolioService(self._config_path.parent),
            )
        return AgentToolRuntime(skill_tools=self._skill_service.toolbox(agent))

    def _load_platform(self) -> AgentPlatformDefinition:
        platform = load_settings(self._config_path).agent_platform
        return self._platform_with_skill_frontmatter(platform)

    def _platform_with_skill_frontmatter(
        self, platform: AgentPlatformDefinition
    ) -> AgentPlatformDefinition:
        if not platform.skill_definitions:
            return platform
        skill_definitions: list[SkillDefinition] = []
        for skill in platform.skill_definitions:
            metadata = self._skill_service.read_workspace_skill(skill.id)
            metadata_name = metadata.name if metadata.content or metadata.name != skill.id else ""
            tools = metadata.tools
            skill_definitions.append(
                SkillDefinition(
                    id=skill.id,
                    name=metadata_name or skill.name,
                    tools=tools,
                )
            )
        return AgentPlatformDefinition(
            models=platform.models,
            default_model_id=platform.default_model_id,
            agents=platform.agents,
            skill_definitions=tuple(skill_definitions),
        )

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
        return self._bound_model_option_by_id(platform, agent.model_id)

    def _bound_model_option_by_id(
        self,
        platform: AgentPlatformDefinition,
        model_id: str,
    ) -> BoundModelOption | None:
        if not model_id:
            return None
        try:
            model = platform.get_model(model_id)
        except AgentConfigError:
            return None
        return BoundModelOption(
            id=model.id,
            name=model.name,
            model=model.model,
            base_url=model.base_url,
            mode=model.mode,
            supports_images=model.supports_images,
            has_api_key=self._has_usable_api_key(model),
        )

    @staticmethod
    def _model_for_agent(
        platform: AgentPlatformDefinition,
        agent: AgentDefinition,
    ) -> ModelDefinition:
        if not agent.model_id:
            raise AgentChatUnavailableError("Agent 未配置模型")
        return platform.get_model(agent.model_id)

    def _has_usable_api_key(self, model: ModelDefinition) -> bool:
        return bool(model.api_key.strip()) and model.api_key.strip() != "change-me"

    def _optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value)

    def _normalize_tool_access(self, raw: dict[str, Any]) -> dict[str, object]:
        unsupported = set(raw) - {"allow"}
        if unsupported:
            raise AgentConfigError(f"legacy tools.{sorted(unsupported)[0]} is not supported")
        allow = raw.get("allow") or []
        if not isinstance(allow, list):
            raise AgentConfigError("tools.allow must be a list")
        return {
            "allow": [str(tool).strip() for tool in allow if str(tool).strip()],
        }

    def _tool_access_from_payload(self, raw: dict[str, object]) -> ToolAccessDefinition:
        normalized = self._normalize_tool_access(raw)
        return ToolAccessDefinition(
            allow=tuple(normalized["allow"]),
        )
