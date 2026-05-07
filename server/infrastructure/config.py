from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from server.domain.agents import (
    AgentDefinition,
    AgentPlatformDefinition,
    ModelDefinition,
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
class Settings:
    auth: AuthConfig
    proxy: ProxyConfig
    server: ServerConfig
    agent_platform: AgentPlatformDefinition = field(
        default_factory=lambda: AgentPlatformDefinition(
            models=(),
            default_model_id="",
            agents=(),
            default_agent_id="",
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

    token = str(auth_raw.get("token") or "").strip()
    upstream_base_url = str(proxy_raw.get("upstream_base_url") or "").strip()
    if not token:
        raise ValueError("auth.token is required")
    if not upstream_base_url:
        raise ValueError("proxy.upstream_base_url is required")

    return Settings(
        auth=AuthConfig(token=token),
        proxy=ProxyConfig(upstream_base_url=upstream_base_url),
        server=ServerConfig(
            host=str(server_raw.get("host") or "0.0.0.0"),
            port=int(server_raw.get("port") or 8888),
        ),
        agent_platform=parse_agent_platform(raw),
    )


def parse_agent_platform(raw: dict[str, Any]) -> AgentPlatformDefinition:
    llm_raw = raw.get("llm") or {}
    agents_raw = raw.get("agents") or {}
    common_skills_raw = raw.get("common_skills") or {}
    models_raw = llm_raw.get("models") or []
    definitions_raw = agents_raw.get("definitions") or []
    tools_raw = common_skills_raw.get("tools") or []
    if not isinstance(models_raw, list):
        raise ValueError("llm.models must be a list")
    if not isinstance(definitions_raw, list):
        raise ValueError("agents.definitions must be a list")
    if not isinstance(tools_raw, list):
        raise ValueError("common_skills.tools must be a list")

    models = tuple(parse_model_definition(item) for item in models_raw)
    agents = tuple(parse_agent_definition(item) for item in definitions_raw)
    common_skill_tools = tuple(str(tool).strip() for tool in tools_raw if str(tool).strip())
    default_model_id = str(llm_raw.get("default_model_id") or (models[0].id if models else "")).strip()
    default_agent_id = str(
        agents_raw.get("default_agent_id") or (agents[0].id if agents else "")
    ).strip()
    return AgentPlatformDefinition(
        models=models,
        default_model_id=default_model_id,
        agents=agents,
        default_agent_id=default_agent_id,
        common_skill_tools=common_skill_tools,
    )


def parse_model_definition(raw: Any) -> ModelDefinition:
    if not isinstance(raw, dict):
        raise ValueError("llm.models[] must be an object")
    temperature_raw = raw.get("temperature")
    return ModelDefinition(
        id=str(raw.get("id") or "").strip(),
        name=str(raw.get("name") or "").strip(),
        base_url=str(raw.get("base_url") or "").strip(),
        api_key=str(raw.get("api_key") or "").strip(),
        model=str(raw.get("model") or "").strip(),
        temperature=float(temperature_raw) if temperature_raw is not None else None,
        supports_images=bool(raw.get("supports_images", False)),
    )


def parse_agent_definition(raw: Any) -> AgentDefinition:
    if not isinstance(raw, dict):
        raise ValueError("agents.definitions[] must be an object")
    model_id = raw.get("model_id")
    skill_ids_raw = raw.get("skill_ids") or []
    if not isinstance(skill_ids_raw, list):
        raise ValueError("agents.definitions[].skill_ids must be a list")
    return AgentDefinition(
        id=str(raw.get("id") or "").strip(),
        name=str(raw.get("name") or "").strip(),
        system_prompt=str(raw.get("system_prompt") or "").strip(),
        model_id=str(model_id).strip() if model_id is not None else None,
        skill_ids=tuple(str(skill_id).strip() for skill_id in skill_ids_raw if str(skill_id).strip()),
    )
