from __future__ import annotations

from typing import Any


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
