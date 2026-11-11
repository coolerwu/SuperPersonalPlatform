from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def cli_path() -> str | None:
    """Resolve the CLI binary the SDK spawns.

    The SDK prefers the wheel-bundled binary; platform-side plugin management
    must use the same one so marketplace/plugin files stay compatible.
    """
    import claude_agent_sdk

    package = Path(claude_agent_sdk.__file__).resolve().parent
    name = "claude.exe" if os.name == "nt" else "claude"
    bundled = package / "_bundled" / name
    if bundled.is_file():
        return str(bundled)
    return shutil.which("claude")


def sdk_version() -> str:
    import claude_agent_sdk

    return str(getattr(claude_agent_sdk, "__version__", "unknown"))


def claude_client_class() -> Any:
    from claude_agent_sdk import ClaudeSDKClient

    return ClaudeSDKClient


def options_class() -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions


def list_sessions(directory: str | None = None) -> list[Any]:
    # ``directory=None`` scans every project dir, which is what the session
    # catalog needs: ``CLAUDE_CODE_PROJECT_DIR_NAME`` decouples the on-disk
    # project dir name from cwd, so filtering by cwd would miss our sessions.
    from claude_agent_sdk import list_sessions as _list_sessions

    return list(_list_sessions(directory=directory))


def get_session_messages(session_id: str) -> list[Any]:
    from claude_agent_sdk import get_session_messages as _get_messages

    return list(_get_messages(session_id))


def rename_session(session_id: str, title: str) -> None:
    from claude_agent_sdk import rename_session as _rename

    _rename(session_id, title)


def delete_session(session_id: str, directory: str | None = None) -> None:
    # The SDK validates the UUID and removes both the transcript and its
    # sibling subagent transcript directory.
    from claude_agent_sdk import delete_session as _delete

    _delete(session_id, directory=directory)
