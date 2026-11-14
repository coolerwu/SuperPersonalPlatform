"""Read-only access to files an Agent produced inside its workspace.

A relative image the Agent writes (``![图](chart.png)``) points at
``workspaces/<agent_id>/chart.png``, which the browser cannot fetch. The chat
resolves such paths to the download route; this module decides what may be
served and whether it must be forced into a download.

Scriptable media types are served as ``application/octet-stream`` with
``Content-Disposition: attachment``: a workspace HTML/SVG/JS file rendered
inline on the app origin could read the login cookie.
"""

from __future__ import annotations

import mimetypes
import unicodedata
from pathlib import Path

from app.services.claude_env import workspace_for
from app.services.identifiers import valid_agent_id
from app.services.workspace import WorkspaceError, normalize_workspace_path

_UNSAFE_INLINE = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "application/javascript",
    "application/x-javascript",
    "text/javascript",
    "application/ecmascript",
    "text/ecmascript",
    "application/xml",
    "text/xml",
}

_PREVIEW_LIMIT = 512 * 1024
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}
# Mirrored in apps/web/src/artifactTypes.ts (TEXT_SUFFIXES).
_TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".svg", ".html", ".htm", ".css", ".scss",
    ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".bash", ".zsh",
    ".sql", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp",
    ".rb", ".php", ".lua", ".swift", ".kt", ".r", ".m", ".tex",
}


def preview_format(path: Path) -> str | None:
    """``"markdown"``/``"text"`` for previewable files, else ``None``."""
    suffix = path.suffix.lower()
    if suffix in _MARKDOWN_EXTENSIONS:
        return "markdown"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    return None


def read_text_preview(path: Path) -> tuple[str, bool, int]:
    """Return ``(content, truncated, size)`` for a vetted regular file.

    ``path`` must come from :func:`resolve_workspace_file`; filesystem errors
    (missing, directory, permissions) propagate as ``OSError``. Invalid UTF-8
    is replaced with U+FFFD instead of raising.
    """
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw = handle.read(_PREVIEW_LIMIT + 1)
    truncated = len(raw) > _PREVIEW_LIMIT
    if truncated:
        raw = raw[:_PREVIEW_LIMIT]
    content = raw.decode("utf-8-sig", errors="replace")
    return content, truncated, size


def resolve_workspace_file(
    agent_id: str, relative_path: str, *, home: Path | None = None
) -> Path | None:
    """Resolve a workspace-relative file; ``None`` when it cannot be served."""
    if not valid_agent_id(agent_id):
        return None
    value = unicodedata.normalize("NFC", str(relative_path or "").strip())
    if value.startswith("./"):
        value = value[2:]
    try:
        normalize_workspace_path(value)
    except WorkspaceError:
        return None
    workspace = workspace_for(agent_id, home)
    if not workspace.is_dir():
        return None
    raw = workspace / value
    # Same rule as resolve_workspace_path: no symlink at the final component.
    if raw.is_symlink():
        return None
    candidate = raw.resolve(strict=False)
    if not candidate.is_relative_to(workspace.resolve()):
        return None
    if not candidate.is_file():
        return None
    return candidate


def media_type_for(path: Path) -> tuple[str, bool]:
    """``(media_type, must_download)`` for a workspace file."""
    guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if guessed in _UNSAFE_INLINE:
        return "application/octet-stream", True
    return guessed, False
