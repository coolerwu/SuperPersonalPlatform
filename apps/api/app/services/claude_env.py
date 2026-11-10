"""Claude Agent SDK environment construction.

SDK configuration and scratch space live under ``APP_HOME``: the config
directory is ``<home>/claude`` and scratch space is ``<home>/tmp``. ``HOME`` is
intentionally *not* overridden so shell tooling keeps using the real user home
and its caches; only ``CLAUDE_CONFIG_DIR`` and ``TMPDIR`` are redirected.

The SDK's in-process session APIs (``list_sessions`` / ``get_session_messages``)
read ``CLAUDE_CONFIG_DIR`` from the *process* environment, while CLI
subprocesses read the env passed in ``options.env``. Both must point at the same
directory, so startup should call :func:`install_claude_process_env` in addition
to passing :func:`build_claude_env` to the SDK.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.config import APP_HOME, settings as app_settings
from app.services.settings_store import AgentSection


def claude_paths(home: Path | None = None) -> dict[str, Path]:
    if home is not None:
        root = home.expanduser()
        workspaces = root / "workspaces"
    else:
        root = APP_HOME.expanduser()
        workspaces = app_settings.workspaces_dir.expanduser()
    return {
        "home": root,
        "config": root / "claude",
        "tmp": root / "tmp",
        "workspaces": workspaces,
    }


def workspace_for(agent_id: str, home: Path | None = None) -> Path:
    return claude_paths(home)["workspaces"] / agent_id


def build_claude_env(
    agent: AgentSection, *, home: Path | None = None
) -> dict[str, str]:
    paths = claude_paths(home)
    provider = agent.provider
    model = provider.model.strip()
    # DeepSeek's ``[1m]`` suffix selects the long-context variant. The Haiku
    # and subagent aliases use the plain model id without that suffix.
    alias_model = model.removesuffix("[1m]")
    env = {
        "CLAUDE_CONFIG_DIR": str(paths["config"]),
        "CLAUDE_CODE_PROJECT_DIR_NAME": agent.id,
        "TMPDIR": str(paths["tmp"]),
        "ANTHROPIC_BASE_URL": provider.base_url.strip(),
        "ANTHROPIC_AUTH_TOKEN": provider.api_key.strip(),
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": alias_model,
        "CLAUDE_CODE_SUBAGENT_MODEL": alias_model,
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
    }
    return {key: value for key, value in env.items() if value}


def install_claude_process_env(home: Path | None = None) -> Path:
    """Force the process-level SDK config dir and return it.

    Uses assignment rather than ``setdefault`` so an existing real
    ``~/.claude`` value cannot win. Idempotent.
    """
    config_dir = claude_paths(home)["config"]
    os.environ["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return config_dir


def ensure_claude_dirs(home: Path | None = None) -> None:
    paths = claude_paths(home)
    for key in ("config", "tmp", "workspaces"):
        paths[key].mkdir(parents=True, exist_ok=True)
    # The SDK config holds credentials and session transcripts, and the
    # scratch/workspace roots hold user files. Keep all three private.
    for key in ("config", "tmp", "workspaces"):
        paths[key].chmod(0o700)
