"""Workspace attachments: safe names, upload paths, and model image blocks.

Attachments remain plain files under ``workspaces/<agent_id>/uploads``. The
message itself only carries their names, workspace paths, and sizes so the
history stays text-only; this module turns those references back into image
content blocks when a Run starts.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.workspace import WorkspaceError, resolve_workspace_path

logger = logging.getLogger(__name__)

ATTACHMENT_HEADER = "附件已上传到工作区："
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_NAME_BYTES = 180
MAX_MODEL_IMAGES = 4
MAX_MODEL_IMAGE_BYTES = 4 * 1024 * 1024

_ATTACHMENT_FILE = re.compile(
    r"^- (?P<name>.+?)（工作区路径：(?P<path>.+?)，大小：(?P<size>\d+)\s*字节）$"
)
_ATTACHMENT_FILE_NO_SIZE = re.compile(
    r"^- (?P<name>.+?)（工作区路径：(?P<path>.+)）$"
)
_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".heic",
    ".heif",
    ".avif",
}
_UNSAFE_FILENAME = re.compile(r"[/\\\x00-\x1f\x7f]")


@dataclass(frozen=True)
class MessageFile:
    name: str
    path: str
    size: int | None


@dataclass(frozen=True)
class SavedAttachment:
    name: str
    path: str
    size: int


def safe_attachment_name(raw: str) -> str:
    """Normalize a display name and keep it usable as a single path segment."""
    value = unicodedata.normalize("NFC", str(raw or "").replace("\\", "/"))
    name = Path(value).name.strip()
    name = "".join(char for char in name if unicodedata.category(char) not in ("Cc", "Cf"))
    name = name.lstrip(".") or "attachment"
    while len(name.encode("utf-8")) > MAX_ATTACHMENT_NAME_BYTES:
        name = name[:-1]
    return name or "attachment"


def is_image_name(name: str) -> bool:
    return Path(name or "").suffix.lower() in _IMAGE_SUFFIXES


def detect_image_mime(data: bytes) -> str | None:
    """Detect common image formats by magic bytes instead of trusting a suffix."""
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12].lower()
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
            return "image/heic"
        if brand in (b"avif", b"avis"):
            return "image/avif"
    return None


def extension_for_mime(mime: str) -> str:
    extension = mimetypes.guess_extension(mime or "")
    if extension == ".jpe":
        return ".jpg"
    return extension or ".bin"


def parse_attachment_files(content: str) -> list[MessageFile]:
    """Parse the attachment header written by the web UI or the channel runtime."""
    lines = (content or "").splitlines()
    if not lines or lines[0].strip() != ATTACHMENT_HEADER:
        return []
    files: list[MessageFile] = []
    for line in lines[1:]:
        match = _ATTACHMENT_FILE.match(line.strip())
        sized = True
        if match is None:
            match = _ATTACHMENT_FILE_NO_SIZE.match(line.strip())
            sized = False
        if match is None:
            break
        size_text = match.groupdict().get("size")
        files.append(
            MessageFile(
                name=match.group("name"),
                path=match.group("path"),
                size=int(size_text) if sized and size_text is not None else None,
            )
        )
    return files


def build_attachment_header(files: list[SavedAttachment]) -> str:
    if not files:
        return ""
    lines = [ATTACHMENT_HEADER]
    lines.extend(
        f"- {file.name}（工作区路径：{file.path}，大小：{file.size} 字节）" for file in files
    )
    return "\n".join(lines)


def title_from_content(content: str) -> str:
    """A readable session title even when the first message is attachment-only."""
    text = (content or "").strip()
    if not text:
        return "新会话"
    files = parse_attachment_files(text)
    if files:
        lines = text.splitlines()
        body = "\n".join(lines[len(files) + 1 :]).strip()
        if body:
            return body.splitlines()[0].strip()[:24] or "新会话"
        suffix = f" 等 {len(files)} 个文件" if len(files) > 1 else ""
        return f"{files[0].name}{suffix}"[:24]
    return text.splitlines()[0].strip()[:24] or "新会话"


def save_attachment_bytes(
    agent_id: str,
    stored_name: str,
    data: bytes,
    *,
    reuse_existing: bool = False,
    display_name: str | None = None,
) -> SavedAttachment:
    uploads = resolve_workspace_path(f"{agent_id}/uploads", create=True)
    name = safe_attachment_name(stored_name)
    target = uploads / name
    if target.exists() and not reuse_existing:
        # The random prefix collision is unlikely, but a fresh upload must not
        # silently reuse another file's bytes.
        name = safe_attachment_name(f"{uuid.uuid4().hex[:10]}-{name}")
        target = uploads / name
    if not target.exists():
        try:
            with target.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            # A replayed WeChat message may have saved the same deterministic
            # name already; keep the existing copy.
            pass
    return SavedAttachment(
        name=display_name or target.name,
        path=f"/uploads/{target.name}",
        size=target.stat().st_size,
    )


def new_stored_name(display_name: str) -> str:
    return safe_attachment_name(f"{uuid.uuid4().hex[:10]}-{display_name}")


def resolve_upload_file(agent_id: str, filename: str) -> Path | None:
    """Resolve one file directly under the agent's uploads directory."""
    name = safe_attachment_name(filename)
    if name != filename or _UNSAFE_FILENAME.search(filename):
        return None
    try:
        uploads = resolve_workspace_path(f"{agent_id}/uploads", create=False)
    except WorkspaceError:
        return None
    if not uploads.is_dir():
        return None
    target = uploads / name
    try:
        resolved = target.resolve(strict=False)
    except OSError:
        return None
    if resolved.parent != uploads.resolve() or not resolved.is_file():
        return None
    return resolved


def _attachment_file(workspace: Path, workspace_path: str) -> Path | None:
    value = (workspace_path or "").strip()
    if not value.startswith("/uploads/"):
        return None
    name = value[len("/uploads/") :]
    if "/" in name or "\\" in name or not name:
        return None
    target = workspace / "uploads" / name
    try:
        resolved = target.resolve(strict=False)
    except OSError:
        return None
    if not resolved.is_relative_to(workspace.resolve()) or not resolved.is_file():
        return None
    return resolved


def build_model_content(
    content: str,
    workspace: Path,
    *,
    max_images: int = MAX_MODEL_IMAGES,
    max_image_bytes: int = MAX_MODEL_IMAGE_BYTES,
) -> str | list[dict[str, Any]]:
    """Attach current-turn images as OpenAI-compatible blocks when possible.

    The database and checkpoint keep only the text form; if the model cannot
    read images the Run fails instead of silently losing the files, which is
    the documented v1 behavior.
    """
    files = parse_attachment_files(content)
    if not files:
        return content
    blocks: list[dict[str, Any]] = []
    for file in files:
        if len(blocks) >= max_images:
            break
        path = _attachment_file(workspace, file.path)
        if path is None:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            logger.warning("cannot read attachment for model: %s", path)
            continue
        if len(data) > max_image_bytes:
            logger.warning("attachment too large for model: %s (%d bytes)", path, len(data))
            continue
        mime = detect_image_mime(data)
        if mime is None:
            continue
        encoded = base64.b64encode(data).decode("ascii")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            }
        )
    if not blocks:
        return content
    return [{"type": "text", "text": content}, *blocks]
