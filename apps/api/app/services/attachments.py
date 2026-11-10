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
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from app.config import settings
from app.services.workspace import WorkspaceError, resolve_workspace_path

logger = logging.getLogger(__name__)

ATTACHMENT_HEADER = "附件已上传到工作区："
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_NAME_BYTES = 180
MAX_MODEL_IMAGES = 4
MAX_MODEL_IMAGE_BYTES = 4 * 1024 * 1024
MAX_LISTED_SKIPPED_IMAGES = 5

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
        # Relative to the Agent workspace, which is the SDK's cwd. The model
        # resolves paths against the real filesystem, so a leading "/" would
        # point at the host root and never match the uploaded file.
        path=f"uploads/{target.name}",
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
    # New messages carry a path relative to the Agent workspace ("uploads/x");
    # old transcripts may still hold the previous absolute form ("/uploads/x").
    # Accept both so history remains readable, but resolve only inside the
    # workspace uploads directory.
    if value.startswith("/uploads/"):
        name = value[len("/uploads/") :]
    elif value.startswith("uploads/"):
        name = value[len("uploads/") :]
    else:
        return None
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


def _size_label(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} 字节"


IMAGE_QUALITY_STEPS = (85, 75, 65)
IMAGE_MIN_EDGE = 640
MAX_DECODE_PIXELS = 50_000_000


def _flatten_image(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def compress_image_for_model(
    data: bytes, *, max_bytes: int, max_edge: int
) -> bytes | None:
    """Re-encode an oversized image as JPEG under the model input limits.

    Returns None when Pillow cannot decode the image or it still exceeds the
    byte budget at the smallest allowed edge; callers fall back to the
    invisible-image note instead of silently dropping it.
    """
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > MAX_DECODE_PIXELS:
                logger.warning(
                    "image too large to decode for model compression: %sx%s",
                    source.width,
                    source.height,
                )
                return None
            if getattr(source, "is_animated", False):
                source.seek(0)
            source.load()
            image = ImageOps.exif_transpose(source)
            image.load()
    except Exception as exc:  # Pillow raises several unrelated types
        logger.warning("cannot decode image for model compression: %s", exc)
        return None
    image = _flatten_image(image)
    edge = max(320, int(max_edge))
    while True:
        longest = max(image.size)
        if longest > edge:
            ratio = edge / longest
            resized = image.resize(
                (
                    max(1, round(image.width * ratio)),
                    max(1, round(image.height * ratio)),
                ),
                Image.Resampling.LANCZOS,
            )
        else:
            resized = image
        for quality in IMAGE_QUALITY_STEPS:
            buffer = BytesIO()
            resized.save(
                buffer,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
            if buffer.tell() <= max_bytes:
                return buffer.getvalue()
        if edge <= IMAGE_MIN_EDGE:
            return None
        edge = max(IMAGE_MIN_EDGE, int(edge * 0.8))


def _skipped_images_note(skipped: list[tuple[str, str]]) -> str:
    """Tell the model about images that could not become image blocks.

    The model would otherwise believe the turn had no images at all and could
    claim to have looked at them; keep the note explicit about what it cannot
    see and how the user can fix it.
    """
    if not skipped:
        return ""
    lines = ["", "（系统提示：以下图片没有加入本次模型输入，你看不到它们："]
    for label, reason in skipped[:MAX_LISTED_SKIPPED_IMAGES]:
        lines.append(f"- {label}：{reason}")
    remaining = len(skipped) - MAX_LISTED_SKIPPED_IMAGES
    if remaining > 0:
        lines.append(f"- 另有 {remaining} 张图片未列出。")
    lines.append(
        "如需查看，请让用户压缩后或改用普通（非原图）方式重新发送图片；"
        "不要声称你看到过这些图片。）"
    )
    return "\n".join(lines)


def _is_image_file(path: Path, name: str) -> bool:
    """Suffix check with a cheap magic-byte fallback for misnamed images."""
    if is_image_name(name):
        return True
    try:
        with path.open("rb") as handle:
            head = handle.read(32)
    except OSError:
        return False
    return detect_image_mime(head) is not None


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
    the documented v1 behavior. Images that cannot be attached are named in a
    system note so the model knows they exist but are invisible.
    """
    files = parse_attachment_files(content)
    if not files:
        return content
    blocks: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    for file in files:
        path = _attachment_file(workspace, file.path)
        if path is None:
            continue
        label = f"{file.name}（{file.path}）"
        if len(blocks) >= max_images:
            if _is_image_file(path, file.name):
                skipped.append((label, f"超过单轮 {max_images} 张图片上限"))
            continue
        try:
            data = path.read_bytes()
        except OSError:
            logger.warning("cannot read attachment for model: %s", path)
            if is_image_name(file.name):
                skipped.append((label, "读取失败"))
            continue
        mime = detect_image_mime(data)
        if mime is None:
            if is_image_name(file.name):
                skipped.append((label, "图片格式无法识别"))
            continue
        if len(data) > max_image_bytes:
            compressed = compress_image_for_model(
                data,
                max_bytes=max_image_bytes,
                max_edge=settings.vision_max_image_edge,
            )
            if compressed is None:
                logger.warning(
                    "attachment too large and not compressible: %s (%d bytes)",
                    path,
                    len(data),
                )
                skipped.append(
                    (
                        label,
                        f"大小 {_size_label(len(data))}，超过单张 "
                        f"{_size_label(max_image_bytes)} 上限，且无法压缩到限制内",
                    )
                )
                continue
            logger.info(
                "compressed attachment for model: %s (%d -> %d bytes)",
                path,
                len(data),
                len(compressed),
            )
            data = compressed
            mime = "image/jpeg"
        encoded = base64.b64encode(data).decode("ascii")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            }
        )
    text_content = f"{content}{_skipped_images_note(skipped)}"
    if not blocks:
        return text_content
    return [{"type": "text", "text": text_content}, *blocks]
