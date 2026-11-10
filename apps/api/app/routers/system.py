from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app import APP_VERSION
from app.auth import require_auth
from app.config import settings
from app.services.browser_tools import playwright_installed
from app.services.claude_env import claude_paths
from app.services.settings_store import SettingsStore, get_settings_store

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_auth)])


def database_kind(url: str) -> str:
    scheme = url.split(":", 1)[0]
    return scheme.split("+", 1)[0]


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _transcript_count() -> int:
    root = claude_paths()["config"] / "projects"
    if not root.is_dir():
        return 0
    try:
        return sum(1 for path in root.glob("*/*.jsonl") if path.is_file())
    except OSError:
        return 0


@router.get("")
async def system_info(request: Request) -> dict[str, object]:
    snapshot = await _store(request).get()
    knowledge = getattr(request.app.state, "knowledge", None)
    active_agents = [
        agent for agent in snapshot.agents if agent.status != "archived"
    ]
    return {
        "version": APP_VERSION,
        "execution_core": "claude-agent-sdk",
        "database": database_kind(settings.app_database_url),
        "workspace_root": str(settings.workspaces_dir.expanduser().resolve()),
        "skills_root": str(settings.skills_dir.expanduser().resolve()),
        "run_timeout_seconds": settings.run_timeout_seconds,
        "max_history_messages": settings.max_history_messages,
        "counts": {
            "providers": 1 if active_agents else 0,
            "agents": len(active_agents),
            "sessions": _transcript_count(),
            "messages": 0,
            "runs": 0,
            "channels": len(snapshot.channels),
        },
        "capabilities": {
            "chat": True,
            "multi_agent": True,
            "subagent": True,
            "checkpoints": False,
            "filesystem": True,
            "shell": True,
            "browser": playwright_installed(),
            "skills": True,
            "memory": True,
        },
        "knowledge": knowledge.status()
        if knowledge is not None
        else {"enabled": False, "local_dir": str(settings.knowledge_dir)},
    }
