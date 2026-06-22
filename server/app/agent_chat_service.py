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
    is_builtin: bool = False


@dataclass(frozen=True)
class EditableSkill:
    id: str
    name: str
    tools_allow: tuple[str, ...]
    is_builtin: bool = False


@dataclass(frozen=True)
class AgentConfigSnapshot:
    path: str
    default_model_id: str
    skills: tuple[EditableSkill, ...]
    models: tuple[EditableModel, ...]
    agents: tuple[EditableAgent, ...]


# ── Built-in agents ───────────────────────────────────────────────────

BUILTIN_AGENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "ai-investment-advisor",
        "name": "AI投资助手",
        "is_builtin": True,
        "skill_ids": ("common:portfolio",),
        "system_prompt": (
            "你是一个专业的投资组合助手，帮助用户管理投资持仓。\n\n"
            "你可以做的操作：\n"
            "1. 查看当前所有持仓\n"
            "2. 添加新的持仓（股票、基金、加密货币）\n"
            "3. 修改现有持仓\n"
            "4. 删除持仓\n\n"
            "持仓数据包含：类型、代码、名称、数量、均价、货币。\n"
            "用户可以用自然语言描述要做的操作，你需要解析并执行。\n"
            "如需获取当前行情信息，请结合你的知识或建议用户补充。\n"
            "请用中文回复，回答简洁专业。"
        ),
    },
)

BUILTIN_SKILLS: tuple[dict[str, Any], ...] = (
    {
        "id": "common:portfolio",
        "name": "投资组合工具",
        "tools_allow": (
            "list_portfolio_holdings",
            "add_portfolio_holding",
            "update_portfolio_holding",
            "delete_portfolio_holding",
        ),
    },
)


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
        config_agents = tuple(
            AgentOption(
                id=agent.id,
                name=agent.name,
                model_id=agent.model_id,
                model=self._bound_model_option(platform, agent),
            )
            for agent in platform.agents
        )
        # Append built-in agents
        builtin_agents = tuple(
            AgentOption(
                id=ba["id"],
                name=ba["name"],
                model_id=ba.get("model_id") or platform.default_model_id or None,
                model=self._bound_model_option_by_id(
                    platform, ba.get("model_id") or platform.default_model_id or ""
                ),
            )
            for ba in self._builtin_with_overrides()
            if all(ba["id"] != ca.id for ca in config_agents)
        )
        return AgentOptions(
            agents=config_agents + builtin_agents,
        )

    def tool_definitions(self) -> tuple[dict[str, object], ...]:
        return self._tool_registry.public_definitions()

    def config_snapshot(self) -> AgentConfigSnapshot:
        platform = self._load_platform()

        # Config-defined agents
        config_agents = tuple(
            EditableAgent(
                id=agent.id,
                name=agent.name,
                model_id=agent.model_id,
                system_prompt=agent.system_prompt,
                skill_ids=agent.skill_ids,
            )
            for agent in platform.agents
        )

        # Append built-in agents (only if not already in config)
        config_ids = {a.id for a in config_agents}
        builtin_agents = tuple(
            EditableAgent(
                id=ba["id"],
                name=ba["name"],
                model_id=ba.get("model_id") or platform.default_model_id or None,
                system_prompt=ba.get("system_prompt", ""),
                skill_ids=tuple(ba.get("skill_ids") or ()),
                is_builtin=True,
            )
            for ba in self._builtin_with_overrides()
            if ba["id"] not in config_ids
        )

        return AgentConfigSnapshot(
            path=str(self._config_path),
            default_model_id=platform.default_model_id,
            skills=tuple(
                EditableSkill(
                    id=skill.id,
                    name=skill.name,
                    tools_allow=skill.tools.allow,
                    is_builtin=skill.id in {item["id"] for item in BUILTIN_SKILLS},
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
            agents=config_agents + builtin_agents,
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

        builtin_ids = {ba["id"] for ba in BUILTIN_AGENTS}
        normalized_agents: list[dict[str, Any]] = []
        builtin_overrides: dict[str, dict[str, Any]] = {}
        for agent in agents:
            if not isinstance(agent, dict):
                raise AgentConfigError("agents[] must be an object")
            agent_id = str(agent.get("id") or "").strip()
            model_id = agent.get("model_id")
            raw_skill_ids = agent.get("skill_ids")

            # Built-in agents: collect overrides, skip from definitions
            if agent_id in builtin_ids:
                override: dict[str, Any] = {}
                system_prompt = str(agent.get("system_prompt") or "").strip()
                if system_prompt:
                    override["system_prompt"] = system_prompt
                if model_id is not None:
                    override["model_id"] = str(model_id).strip() or ""
                if override:
                    builtin_overrides[agent_id] = override
                continue

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
        raw["agents"]["definitions"] = normalized_agents
        if builtin_overrides:
            raw["agents"]["builtin_overrides"] = builtin_overrides
        elif "builtin_overrides" in raw.get("agents", {}):
            del raw["agents"]["builtin_overrides"]
        raw.pop("common_skills", None)
        raw.pop("tools", None)
        parse_settings(raw)
        self._write_raw_config(raw)

    def read_skill_content(self, skill_id: str, agent_id: str | None = None) -> dict[str, object]:
        skill = self._skill_service.read_workspace_skill(skill_id, agent_id)
        tools = skill.tools
        if not self._skill_service.workspace_skill_exists(skill_id, agent_id):
            builtin = next((item for item in BUILTIN_SKILLS if item["id"] == skill_id), None)
            if builtin is not None:
                tools = ToolAccessDefinition(allow=tuple(builtin["tools_allow"]))
        return {
            "id": skill.id,
            "name": skill.name,
            "content": skill.content,
            "tools": {
                "allow": list(tools.allow),
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
        platform = self._platform_with_skill_frontmatter(platform)

        # Merge built-in agents that aren't already in config definitions
        builtin_defs = self._builtin_agent_definitions(platform)
        builtin_skills = self._builtin_skill_definitions(platform)
        if builtin_defs or builtin_skills:
            all_agents = platform.agents + tuple(builtin_defs)
            all_skills = platform.skill_definitions + tuple(builtin_skills)
            return AgentPlatformDefinition(
                models=platform.models,
                default_model_id=platform.default_model_id,
                agents=all_agents,
                skill_definitions=all_skills,
            )
        return platform

    def _platform_with_skill_frontmatter(
        self, platform: AgentPlatformDefinition
    ) -> AgentPlatformDefinition:
        if not platform.skill_definitions:
            return platform
        skill_definitions: list[SkillDefinition] = []
        builtin_skill_tools = {
            skill["id"]: ToolAccessDefinition(allow=tuple(skill["tools_allow"]))
            for skill in BUILTIN_SKILLS
        }
        for skill in platform.skill_definitions:
            metadata = self._skill_service.read_workspace_skill(skill.id)
            metadata_name = metadata.name if metadata.content or metadata.name != skill.id else ""
            tools = metadata.tools
            if not metadata.content and skill.id in builtin_skill_tools:
                tools = builtin_skill_tools[skill.id]
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

    def _builtin_agent_definitions(
        self, platform: AgentPlatformDefinition
    ) -> list[AgentDefinition]:
        """Convert built-in agents (with overrides) to AgentDefinition objects."""
        config_ids = {a.id for a in platform.agents}
        definitions: list[AgentDefinition] = []
        for ba in self._builtin_with_overrides():
            if ba["id"] in config_ids:
                continue  # already defined in config.yaml
            model_id = ba.get("model_id") or platform.default_model_id or ""
            definitions.append(
                AgentDefinition(
                    id=ba["id"],
                    name=ba["name"],
                    system_prompt=ba.get("system_prompt", ""),
                    model_id=model_id or None,
                    skill_ids=tuple(ba.get("skill_ids") or ()),
                )
            )
        return definitions

    def _builtin_skill_definitions(
        self, platform: AgentPlatformDefinition
    ) -> list[SkillDefinition]:
        config_ids = {skill.id for skill in platform.skill_definitions}
        definitions: list[SkillDefinition] = []
        for skill in BUILTIN_SKILLS:
            if skill["id"] in config_ids:
                continue
            definitions.append(
                SkillDefinition(
                    id=skill["id"],
                    name=skill.get("name", ""),
                    tools=ToolAccessDefinition(allow=tuple(skill["tools_allow"])),
                )
            )
        return definitions

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

    def _builtin_with_overrides(self) -> list[dict[str, Any]]:
        """Return built-in agent definitions with config.yaml overrides applied."""
        raw = self._read_raw_config()
        overrides = raw.get("agents", {}).get("builtin_overrides", {})
        result: list[dict[str, Any]] = []
        for template in BUILTIN_AGENTS:
            agent = dict(template)  # shallow copy
            aid = agent["id"]
            if aid in overrides:
                override = overrides[aid]
                if "system_prompt" in override:
                    agent["system_prompt"] = override["system_prompt"]
                if "model_id" in override:
                    agent["model_id"] = override["model_id"]
            result.append(agent)
        return result

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
