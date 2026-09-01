from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from server.domain.agent_config import (
    AgentConfigError,
    AgentDefinition,
    AgentWorkspaceDefinition,
    DeepAgentFilesystemOptions,
    DeepAgentOptions,
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
class BrowserConfig:
    proxy: str = ""
    timeout_ms: int = 60000
    allow_private_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.timeout_ms < 1000:
            raise ValueError("browser.timeout_ms must be at least 1000")
        for host in self.allow_private_hosts:
            if not host or "://" in host or "/" in host:
                raise ValueError("browser.allow_private_hosts[] must be hostnames, not URLs")


@dataclass(frozen=True)
class NutstoreConfig:
    enabled: bool = False
    base_url: str = "https://dav.jianguoyun.com/dav/"
    username: str = ""
    password: str = ""
    root_path: str = "/"


@dataclass(frozen=True)
class WebDAVPermission:
    path: str
    readable: bool = True
    writable: bool = False
    protected: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _normalize_posix_path(self.path))
        if not self.path.startswith("/"):
            raise AgentConfigError("context.webdav_permissions[].path must start with /")
        if self.protected and self.writable:
            raise AgentConfigError(f"context.webdav_permissions[{self.path}] cannot be both protected and writable")


@dataclass(frozen=True)
class WebDAVSyncConfig:
    enabled: bool = False
    root_path: str = "/"
    interval_seconds: int = 600
    max_files_per_root: int = 500
    max_file_size_bytes: int = 524288
    extensions: tuple[str, ...] = (".md", ".txt", ".json", ".jsonl")

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_path", _normalize_posix_path(self.root_path))
        if not self.root_path.startswith("/"):
            raise AgentConfigError("context.webdav_sync.root_path must start with /")
        if self.interval_seconds < 60:
            raise AgentConfigError("context.webdav_sync.interval_seconds must be at least 60")
        if self.max_files_per_root < 1:
            raise AgentConfigError("context.webdav_sync.max_files_per_root must be greater than zero")
        if self.max_file_size_bytes < 1:
            raise AgentConfigError("context.webdav_sync.max_file_size_bytes must be greater than zero")


@dataclass(frozen=True)
class ContextConfig:
    webdav_sync: WebDAVSyncConfig = field(default_factory=WebDAVSyncConfig)
    webdav_permissions: tuple[WebDAVPermission, ...] = ()


@dataclass(frozen=True)
class MaintenanceConfig:
    enabled: bool = True
    interval_seconds: int = 86400
    retention_days: int = 15
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.interval_seconds < 60:
            raise ValueError("maintenance.interval_seconds must be at least 60")
        if self.retention_days < 1:
            raise ValueError("maintenance.retention_days must be at least 1")


@dataclass(frozen=True)
class Settings:
    auth: AuthConfig
    server: ServerConfig
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    nutstore: NutstoreConfig = field(default_factory=NutstoreConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
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
        browser=parse_browser_config(raw.get("browser") or {}),
        nutstore=parse_nutstore_config(nutstore_raw),
        context=parse_context_config(raw.get("context") or {}),
        maintenance=parse_maintenance_config(raw.get("maintenance") or {}),
        agent_workspace=parse_agent_workspace(raw),
    )


def parse_agent_workspace(raw: dict[str, Any]) -> AgentWorkspaceDefinition:
    llm_raw = raw.get("llm") or {}
    agents_raw = raw.get("agents") or {}

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
        deepagent=parse_deepagent_options(raw.get("deepagent") or {}),
    )


def parse_deepagent_options(raw: Any) -> DeepAgentOptions:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("agents.definitions[].deepagent must be an object")
    return DeepAgentOptions(
        max_iterations=int(raw.get("max_iterations") or 60),
        name=str(raw.get("name") or "").strip(),
        debug=bool(raw.get("debug", False)),
        todo_list=bool(raw.get("todo_list", True)),
        filesystem=_parse_deepagent_filesystem(raw.get("filesystem") or {}),
        use_longterm_memory=bool(raw.get("use_longterm_memory", True)),
        tools=_string_tuple(raw.get("tools") or []),
        interrupt_on=_string_tuple(raw.get("interrupt_on") or []),
        middleware=_string_tuple(raw.get("middleware") or []),
        subagents=_dict_tuple(raw.get("subagents") or []),
        response_format=str(raw.get("response_format") or "").strip(),
        context_schema=str(raw.get("context_schema") or "").strip(),
        checkpointer=bool(raw.get("checkpointer", False)),
        store=str(raw.get("store") or "").strip(),
        cache=str(raw.get("cache") or "").strip(),
    )


def _parse_deepagent_filesystem(raw: Any) -> DeepAgentFilesystemOptions:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("agents.definitions[].deepagent.filesystem must be an object")
    return DeepAgentFilesystemOptions(
        enabled=bool(raw.get("enabled", False)),
        root=str(raw.get("root") or "agent").strip() or "agent",
        mode=str(raw.get("mode") or "read_write").strip() or "read_write",
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


def parse_browser_config(raw: Any) -> BrowserConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("browser must be an object")
    return BrowserConfig(
        proxy=str(raw.get("proxy") or "").strip(),
        timeout_ms=int(raw.get("timeout_ms") or 60000),
        allow_private_hosts=_string_tuple(raw.get("allow_private_hosts") or [], field_name="browser.allow_private_hosts"),
    )


def parse_context_config(raw: Any) -> ContextConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("context must be an object")
    sync_raw = raw.get("webdav_sync") or {}
    permissions_raw = raw.get("webdav_permissions")
    if not isinstance(sync_raw, dict):
        raise ValueError("context.webdav_sync must be an object")
    if permissions_raw is not None and not isinstance(permissions_raw, list):
        raise ValueError("context.webdav_permissions must be a list")
    extensions_raw = sync_raw.get("extensions") or [".md", ".txt", ".json", ".jsonl"]
    if not isinstance(extensions_raw, list):
        raise ValueError("context.webdav_sync.extensions must be a list")
    extensions = tuple(_normalize_extension(item) for item in extensions_raw if str(item).strip())
    root_path = str(sync_raw.get("root_path") or "/").strip() or "/"
    permissions = tuple(_parse_webdav_permission(item) for item in (permissions_raw or []))
    permission_paths = {permission.path for permission in permissions}
    if len(permission_paths) != len(permissions):
        raise AgentConfigError("context.webdav_permissions[].path must be unique")
    return ContextConfig(
        webdav_sync=WebDAVSyncConfig(
            enabled=bool(sync_raw.get("enabled", False)),
            root_path=root_path,
            interval_seconds=int(sync_raw.get("interval_seconds") or 600),
            max_files_per_root=int(sync_raw.get("max_files_per_root") or 500),
            max_file_size_bytes=int(sync_raw.get("max_file_size_bytes") or 524288),
            extensions=extensions or (".md", ".txt", ".json", ".jsonl"),
        ),
        webdav_permissions=permissions,
    )


def parse_maintenance_config(raw: Any) -> MaintenanceConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("maintenance must be an object")
    return MaintenanceConfig(
        enabled=bool(raw.get("enabled", True)),
        interval_seconds=int(raw.get("interval_seconds") or 86400),
        retention_days=int(raw.get("retention_days") or 15),
        dry_run=bool(raw.get("dry_run", False)),
    )


def _parse_webdav_permission(raw: Any) -> WebDAVPermission:
    if not isinstance(raw, dict):
        raise ValueError("context.webdav_permissions[] must be an object")
    return WebDAVPermission(
        path=str(raw.get("path") or "").strip(),
        readable=bool(raw.get("readable", True)),
        writable=bool(raw.get("writable", False)),
        protected=bool(raw.get("protected", False)),
    )


def _normalize_posix_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not raw.startswith("/"):
        return raw
    normalized = "/" + PurePosixPath(raw).as_posix().strip("/")
    return "/" if normalized == "/" else normalized.rstrip("/")


def _normalize_extension(value: Any) -> str:
    extension = str(value or "").strip().lower()
    if not extension:
        raise AgentConfigError("context.webdav_sync.extensions[] is required")
    if not extension.startswith("."):
        extension = "." + extension
    if any(part in extension for part in ("/", "\\")):
        raise AgentConfigError("context.webdav_sync.extensions[] must be a file extension")
    return extension


def _string_tuple(value: Any, *, field_name: str = "deepagent list options") -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _dict_tuple(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise ValueError("deepagent.subagents must be a list")
    return tuple(dict(item) for item in value if isinstance(item, dict))
