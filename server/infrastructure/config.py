from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from server.domain.agents import (
    AgentConfigError,
    AgentDefinition,
    AgentWorkspaceDefinition,
    ModelDefinition,
    ModelProvider,
)


@dataclass(frozen=True)
class AuthConfig:
    token: str


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8888


@dataclass(frozen=True)
class NutstoreConfig:
    enabled: bool = False
    base_url: str = "https://dav.jianguoyun.com/dav/"
    username: str = ""
    password: str = ""
    root_path: str = "/"


@dataclass(frozen=True)
class Settings:
    auth: AuthConfig
    server: ServerConfig
    nutstore: NutstoreConfig = field(default_factory=NutstoreConfig)
    agent_workspace: AgentWorkspaceDefinition = field(
        default_factory=lambda: AgentWorkspaceDefinition(
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
    server_raw = raw.get("server") or {}
    nutstore_raw = raw.get("nutstore") or {}

    token = str(auth_raw.get("token") or "").strip()
    if not token:
        raise ValueError("auth.token is required")

    return Settings(
        auth=AuthConfig(token=token),
        server=ServerConfig(
            host=str(server_raw.get("host") or "0.0.0.0"),
            port=int(server_raw.get("port") or 8888),
        ),
        nutstore=parse_nutstore_config(nutstore_raw),
        agent_workspace=parse_agent_workspace(raw),
    )


def parse_agent_workspace(raw: dict[str, Any]) -> AgentWorkspaceDefinition:
    llm_raw = raw.get("llm") or {}
    agents_raw = raw.get("agents") or {}
    if "skills" in raw or "common_skills" in raw or "tools" in raw:
        raise AgentConfigError("legacy skills/tools configuration is not supported")
    if "portfolio" in raw or "proxy" in raw:
        raise AgentConfigError("legacy portfolio/proxy configuration is not supported")
    if "builtin_overrides" in agents_raw:
        raise AgentConfigError("legacy agents.builtin_overrides is not supported")

    models_raw = llm_raw.get("models") or []
    agents_def_raw = agents_raw.get("definitions") or []
    if not isinstance(models_raw, list):
        raise ValueError("llm.models must be a list")
    if not isinstance(agents_def_raw, list):
        raise ValueError("agents.definitions must be a list")

    models = tuple(parse_model_definition(item) for item in models_raw)
    agents = tuple(parse_agent_definition(item) for item in agents_def_raw)
    default_model_id = str(llm_raw.get("default_model_id") or (models[0].id if models else "")).strip()
    return AgentWorkspaceDefinition(
        models=models,
        default_model_id=default_model_id,
        agents=agents,
    )


def parse_model_definition(raw: Any) -> ModelDefinition:
    if not isinstance(raw, dict):
        raise ValueError("llm.models[] must be an object")
    model_id = str(raw.get("id") or "").strip()
    try:
        provider = ModelProvider(str(raw.get("provider") or ModelProvider.OPENAI_COMPATIBLE.value).strip())
    except ValueError as exc:
        raise AgentConfigError(f"llm.models[{model_id}].provider is unsupported") from exc
    temperature_raw = raw.get("temperature")
    return ModelDefinition(
        id=model_id,
        name=str(raw.get("name") or "").strip(),
        base_url=str(raw.get("base_url") or "").strip(),
        api_key=str(raw.get("api_key") or "").strip(),
        model=str(raw.get("model") or "").strip(),
        provider=provider,
        temperature=float(temperature_raw) if temperature_raw is not None else None,
        supports_images=bool(raw.get("supports_images", False)),
    )


def parse_agent_definition(raw: Any) -> AgentDefinition:
    if not isinstance(raw, dict):
        raise ValueError("agents.definitions[] must be an object")
    if "skill_ids" in raw or "tools" in raw:
        raise AgentConfigError("legacy agents.definitions skill/tool fields are not supported")
    context_ids_raw = raw.get("context_ids") or []
    if not isinstance(context_ids_raw, list):
        raise ValueError("agents.definitions[].context_ids must be a list")
    model_id = raw.get("model_id")
    return AgentDefinition(
        id=str(raw.get("id") or "").strip(),
        name=str(raw.get("name") or "").strip(),
        system_prompt=str(raw.get("system_prompt") or "").strip(),
        model_id=str(model_id).strip() if model_id is not None else None,
        context_ids=tuple(str(context_id).strip() for context_id in context_ids_raw if str(context_id).strip()),
    )


def parse_nutstore_config(raw: Any) -> NutstoreConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("nutstore must be an object")
    return NutstoreConfig(
        enabled=bool(raw.get("enabled", False)),
        base_url=str(raw.get("base_url") or "https://dav.jianguoyun.com/dav/").strip(),
        username=str(raw.get("username") or "").strip(),
        password=str(raw.get("password") or "").strip(),
        root_path=str(raw.get("root_path") or "/").strip() or "/",
    )
