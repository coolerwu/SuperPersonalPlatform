from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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
    )
