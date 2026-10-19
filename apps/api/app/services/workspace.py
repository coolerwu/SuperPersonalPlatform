from __future__ import annotations

import unicodedata
from pathlib import Path

from app.config import settings

MAX_PATH_CHARS = 200
MAX_SEGMENT_BYTES = 255


class WorkspaceError(ValueError):
    """A workspace path or mode the platform refuses to use."""


def normalize_workspace_path(raw: str) -> str:
    value = unicodedata.normalize("NFC", str(raw or "").strip())
    if not value:
        raise WorkspaceError("workspace path is required")
    if "\\" in value:
        raise WorkspaceError("workspace path must use forward slashes")
    if value.startswith(("/", "~")):
        raise WorkspaceError("workspace path must be relative")
    if len(value) >= 2 and value[0].isalpha() and value[1] == ":":
        raise WorkspaceError("workspace path must be relative")
    if len(value) > MAX_PATH_CHARS:
        raise WorkspaceError(f"workspace path must be at most {MAX_PATH_CHARS} characters")

    parts = value.split("/")
    for part in parts:
        if not part or part in (".", ".."):
            raise WorkspaceError("workspace path contains an invalid segment")
        if any(unicodedata.category(char) in ("Cc", "Cf") for char in part):
            raise WorkspaceError("workspace path contains control characters")
        if len(part.encode("utf-8")) > MAX_SEGMENT_BYTES:
            raise WorkspaceError("workspace path segment is too long")
    return "/".join(parts)


def workspace_root() -> Path:
    return settings.workspaces_dir.expanduser().resolve()


def resolve_workspace_path(relative_path: str, *, create: bool) -> Path:
    normalized = normalize_workspace_path(relative_path)
    root = workspace_root()
    raw_candidate = root / normalized
    if raw_candidate.is_symlink():
        raise WorkspaceError("workspace path must not be a symlink")

    candidate = raw_candidate.resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise WorkspaceError("workspace path escapes the workspace root")
    if candidate.exists() and not candidate.is_dir():
        raise WorkspaceError("workspace path exists and is not a directory")

    if create:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"cannot create workspace directory: {exc}") from exc
        candidate = candidate.resolve()
        if not candidate.is_relative_to(root):
            raise WorkspaceError("workspace path escapes the workspace root")
    return candidate


async def ensure_agent_workspaces() -> None:
    """Create `workspaces/<agent_id>` for every existing agent."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Agent

    async with SessionLocal() as db:
        agent_ids = list((await db.scalars(select(Agent.id))).all())
    for agent_id in agent_ids:
        resolve_workspace_path(agent_id, create=True)
