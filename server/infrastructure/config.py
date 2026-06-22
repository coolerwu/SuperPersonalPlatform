from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class AuthConfig:
    token: str


@dataclass(frozen=True)
class ProxyConfig:
    upstream_base_url: str


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8888


@dataclass(frozen=True)
class PortfolioConfig:
    agent_id: str = ""


@dataclass(frozen=True)
class Settings:
    auth: AuthConfig
    proxy: ProxyConfig
    server: ServerConfig
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    agent_platform: AgentPlatformDefinition = field(
        default_factory=lambda: AgentPlatformDefinition(
            models=(),
            default_model_id="",
            agents=(),
        )
    )


def load_settings(config_path: str | Path) -> Settings:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"config file not found: {path}. Copy config.example.yaml to the workspace config.yaml."
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_settings(raw)


def parse_settings(raw: dict[str, Any]) -> Settings:
    auth_raw = raw.get("auth") or {}
    proxy_raw = raw.get("proxy") or {}
    server_raw = raw.get("server") or {}
    portfolio_raw = raw.get("portfolio") or {}

    token = str(auth_raw.get("token") or "").strip()
    upstream_base_url = str(proxy_raw.get("upstream_base_url") or "").strip()
    if not token:
        raise ValueError("auth.token is required")
    if not upstream_base_url:
        raise ValueError("proxy.upstream_base_url is required")

    agent_platform = parse_agent_platform(raw)
    portfolio_agent_id = str(portfolio_raw.get("agent_id") or "").strip()
    if portfolio_agent_id and portfolio_agent_id not in {agent.id for agent in agent_platform.agents}:
        raise AgentConfigError("portfolio.agent_id must reference an existing Agent")

    return Settings(
        auth=AuthConfig(token=token),
        proxy=ProxyConfig(upstream_base_url=upstream_base_url),
        server=ServerConfig(
            host=str(server_raw.get("host") or "0.0.0.0"),
            port=int(server_raw.get("port") or 8888),
        ),
        portfolio=PortfolioConfig(agent_id=portfolio_agent_id),
        agent_platform=agent_platform,
    )


def parse_agent_platform(raw: dict[str, Any]) -> AgentPlatformDefinition:
    llm_raw = raw.get("llm") or {}
    agents_raw = raw.get("agents") or {}
    skills_raw = raw.get("skills") or {}
    if "common_skills" in raw or "tools" in raw:
        raise AgentConfigError("legacy common_skills/tools configuration is not supported")
    if "builtin_overrides" in agents_raw:
        raise AgentConfigError("legacy agents.builtin_overrides is not supported")
    models_raw = llm_raw.get("models") or []
    definitions_raw = agents_raw.get("definitions") or []
    skill_definitions_raw = skills_raw.get("definitions") or []
    if not isinstance(models_raw, list):
        raise ValueError("llm.models must be a list")
    if not isinstance(definitions_raw, list):
        raise ValueError("agents.definitions must be a list")
    if not isinstance(skill_definitions_raw, list):
        raise ValueError("skills.definitions must be a list")

    models = tuple(parse_model_definition(item) for item in models_raw)
    agents = tuple(parse_agent_definition(item) for item in definitions_raw)
    skill_definitions = tuple(parse_skill_definition(item) for item in skill_definitions_raw)
    default_model_id = str(llm_raw.get("default_model_id") or (models[0].id if models else "")).strip()
    return AgentPlatformDefinition(
        models=models,
        default_model_id=default_model_id,
        agents=agents,
        skill_definitions=skill_definitions,
    )


def parse_model_definition(raw: Any) -> ModelDefinition:
    if not isinstance(raw, dict):
        raise ValueError("llm.models[] must be an object")
    temperature_raw = raw.get("temperature")
    provider = str(raw.get("provider") or "openai_compatible").strip()
    model_id = str(raw.get("id") or "").strip()
    try:
        mode = HarnessMode(str(raw.get("mode") or HarnessMode.PROMPT.value).strip())
    except ValueError as exc:
        raise AgentConfigError(f"llm.models[{model_id}].mode is unsupported") from exc
    return ModelDefinition(
        id=model_id,
        name=str(raw.get("name") or "").strip(),
        base_url=str(raw.get("base_url") or "").strip(),
        api_key=str(raw.get("api_key") or "").strip(),
        model=str(raw.get("model") or "").strip(),
        provider=provider or "openai_compatible",
        temperature=float(temperature_raw) if temperature_raw is not None else None,
        supports_images=bool(raw.get("supports_images", False)),
        mode=mode,
    )


def parse_agent_definition(raw: Any) -> AgentDefinition:
    if not isinstance(raw, dict):
        raise ValueError("agents.definitions[] must be an object")
    model_id = raw.get("model_id")
    skill_ids_raw = raw.get("skill_ids") or []
    if "tools" in raw:
        raise AgentConfigError("legacy agents.definitions[].tools is not supported")
    if not isinstance(skill_ids_raw, list):
        raise ValueError("agents.definitions[].skill_ids must be a list")
    return AgentDefinition(
        id=str(raw.get("id") or "").strip(),
        name=str(raw.get("name") or "").strip(),
        system_prompt=str(raw.get("system_prompt") or "").strip(),
        model_id=str(model_id).strip() if model_id is not None else None,
        skill_ids=tuple(str(skill_id).strip() for skill_id in skill_ids_raw if str(skill_id).strip()),
    )


def parse_skill_definition(raw: Any) -> SkillDefinition:
    if not isinstance(raw, dict):
        raise ValueError("skills.definitions[] must be an object")
    tools_raw = raw.get("tools") or {}
    if not isinstance(tools_raw, dict):
        raise ValueError("skills.definitions[].tools must be an object")
    return SkillDefinition(
        id=str(raw.get("id") or "").strip(),
        name=str(raw.get("name") or "").strip(),
        tools=parse_tool_access(tools_raw, "skills.definitions[].tools"),
    )


def parse_tool_access(raw: dict[str, Any], path: str) -> ToolAccessDefinition:
    unsupported = set(raw) - {"allow"}
    if unsupported:
        raise AgentConfigError(f"legacy {path}.{sorted(unsupported)[0]} is not supported")
    allow_raw = raw.get("allow") or []
    if not isinstance(allow_raw, list):
        raise ValueError(f"{path}.allow must be a list")
    return ToolAccessDefinition(
        allow=tuple(str(tool).strip() for tool in allow_raw if str(tool).strip()),
    )
