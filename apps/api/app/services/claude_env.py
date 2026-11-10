"""Claude Agent SDK environment construction.

All SDK state lives under ``APP_HOME``: the SDK config directory is
``<home>/claude`` and its scratch space is ``<home>/tmp``. ``HOME`` is
intentionally *not* overridden so shell tooling keeps using the real user
home and its caches; only ``CLAUDE_CONFIG_DIR`` and ``TMPDIR`` are redirected.
"""

from __future__ import annotations

from pathlib import Path

from app.config import APP_HOME
from app.services.settings_store import AgentSection


def claude_paths(home: Path | None = None) -> dict[str, Path]:
    root = home or APP_HOME
    return {
        "home": root,
        "config": root / "claude",
        "tmp": root / "tmp",
        "workspaces": root / "workspaces",
    }


def workspace_for(agent_id: str, home: Path | None = None) -> Path:
    return claude_paths(home)["workspaces"] / agent_id


def build_claude_env(
    agent: AgentSection, *, home: Path | None = None
) -> dict[str, str]:
    paths = claude_paths(home)
    provider = agent.provider
    env = {
        "CLAUDE_CONFIG_DIR": str(paths["config"]),
        "CLAUDE_CODE_PROJECT_DIR_NAME": agent.id,
        "TMPDIR": str(paths["tmp"]),
        "ANTHROPIC_BASE_URL": provider.base_url,
        "ANTHROPIC_AUTH_TOKEN": provider.api_key,
        "ANTHROPIC_MODEL": provider.model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": provider.model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": provider.model,
        "CLAUDE_CODE_SUBAGENT_MODEL": provider.model,
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
    }
    return {key: value for key, value in env.items() if value}


def ensure_claude_dirs(home: Path | None = None) -> None:
    paths = claude_paths(home)
    for key in ("config", "tmp", "workspaces"):
        paths[key].mkdir(parents=True, exist_ok=True)
