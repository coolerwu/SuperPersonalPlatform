"""Workspace files a turn produced, derived from tool inputs.

Only writes with an explicit path (``Write``/``Edit``/``MultiEdit``/
``NotebookEdit``) and screenshot/save-PDF MCP tools with a ``filename`` are
detected. Files a shell command or an MCP tool creates under another name are
invisible here; that is a documented limit, not a bug to paper over.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Iterable

from app.services.workspace import WorkspaceError, normalize_workspace_path

FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# SVG is a file, not a thumbnail: the download route refuses to render it
# inline, so an <img> would only ever be a broken image.
IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif", ".avif"}
)


def _candidate_path(name: str, tool_input: dict[str, Any]) -> str:
    if name in FILE_TOOLS:
        value = tool_input.get("file_path")
        return value if isinstance(value, str) else ""
    lowered = name.lower()
    if "screenshot" in lowered or "pdf_save" in lowered:
        value = tool_input.get("filename")
        if isinstance(value, str) and value.strip():
            return value
    return ""


def workspace_relative(raw: str, workspace: Path) -> str | None:
    """Normalize an absolute or relative tool path to a workspace-relative one."""
    value = unicodedata.normalize("NFC", str(raw or "").strip())
    if not value:
        return None
    if Path(value).is_absolute():
        try:
            value = (
                Path(value)
                .resolve(strict=False)
                .relative_to(workspace.resolve())
                .as_posix()
            )
        except ValueError:
            return None
    elif value.startswith("./"):
        value = value[2:]
    try:
        return normalize_workspace_path(value)
    except WorkspaceError:
        return None


def artifact_from_tool(
    name: str, tool_input: Any, workspace: Path
) -> dict[str, Any] | None:
    if not isinstance(tool_input, dict):
        return None
    relative = workspace_relative(_candidate_path(name, tool_input), workspace)
    if relative is None:
        return None
    target = workspace.resolve() / relative
    # The download route refuses symlinks; listing one here would only
    # produce an artifact chip that 404s when clicked.
    if target.is_symlink():
        return None
    return {
        "path": relative,
        "name": Path(relative).name,
        "size": target.stat().st_size if target.is_file() else None,
        "kind": (
            "image" if Path(relative).suffix.lower() in IMAGE_SUFFIXES else "file"
        ),
    }


def collect_artifacts(
    blocks: Iterable[Any], workspace: Path
) -> list[dict[str, Any]]:
    """Artifacts in first-seen order; a later write of the same path wins."""
    found: dict[str, dict[str, Any]] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        artifact = artifact_from_tool(
            str(block.get("name") or ""), block.get("input"), workspace
        )
        if artifact is not None:
            found[artifact["path"]] = artifact
    return list(found.values())
