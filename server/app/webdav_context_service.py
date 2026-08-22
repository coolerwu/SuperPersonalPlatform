from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from server.domain.agent_config import AgentConfigError
from server.infrastructure.config import ContextConfig, NutstoreConfig, WebDAVPermission, WebDAVSyncConfig
from server.infrastructure.nutstore_webdav import NutstoreWebDAVClient, WebDAVEntry


class WebDAVContextError(ValueError):
    pass


@dataclass(frozen=True)
class WebDAVContextDocument:
    path: str
    content: str


class WebDAVContextService:
    text_suffixes = {".md", ".txt", ".json", ".jsonl"}
    markdown_asset_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

    def __init__(
        self,
        *,
        workspace: Path,
        nutstore: NutstoreConfig,
        context: ContextConfig,
        client: NutstoreWebDAVClient | None = None,
    ) -> None:
        self._workspace = workspace
        self._nutstore = nutstore
        self._context = context
        self._sync = context.webdav_sync
        self._client = client or NutstoreWebDAVClient(nutstore)
        self._cache_dir = workspace / "context" / "webdav"
        self._files_dir = self._cache_dir / "files"
        self._index_path = self._cache_dir / "index.json"
        self._lock = asyncio.Lock()

    async def refresh_if_stale(self) -> None:
        if not self._sync.enabled or not self._nutstore.enabled:
            return
        if not self._is_stale():
            return
        async with self._lock:
            if self._is_stale():
                await self.refresh()

    async def refresh(self) -> None:
        if not self._sync.enabled or not self._nutstore.enabled:
            return
        previous = _read_json(self._index_path)
        previous_files = previous.get("files") if isinstance(previous, dict) else {}
        if not isinstance(previous_files, dict):
            previous_files = {}
        next_files: dict[str, dict[str, Any]] = {}
        entries = await self._scan_root()
        scan_root = _full_remote_root(self._nutstore.root_path, self._sync.root_path)
        readable_entries: dict[str, tuple[WebDAVEntry, WebDAVPermission]] = {}
        for entry in entries[: self._sync.max_files_per_root]:
            relative_path = _relative_to_root(scan_root, entry.path)
            permission = _permission_for_path("/" + relative_path, self._context.webdav_permissions)
            if permission is None or not permission.readable:
                continue
            readable_entries[relative_path] = (entry, permission)

        referenced_assets: set[str] = set()
        for relative_path, (entry, permission) in readable_entries.items():
            tool_path = _tool_path(relative_path)
            if not _is_text_document(tool_path, self._sync):
                continue
            metadata = _entry_metadata(permission, entry, tool_path)
            metadata["kind"] = "document"
            old_metadata = previous_files.get(tool_path) if isinstance(previous_files, dict) else None
            content_text = ""
            if _metadata_changed(old_metadata, metadata):
                content, truncated = await self._client.read_bytes(entry.path, max_bytes=self._sync.max_file_size_bytes)
                if truncated:
                    continue
                cache_path = self._cache_file_path(tool_path)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    content_text = content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                cache_path.write_text(content_text, encoding="utf-8")
            else:
                try:
                    content_text = self._cache_file_path(tool_path).read_text(encoding="utf-8")
                except OSError:
                    content_text = ""
            if PurePosixPath(relative_path).suffix.lower() == ".md":
                referenced_assets.update(_markdown_asset_references(content_text, relative_path))
            metadata["cache_path"] = self._cache_file_path(tool_path).relative_to(self._cache_dir).as_posix()
            next_files[tool_path] = metadata
        for relative_path in sorted(referenced_assets):
            entry_pair = readable_entries.get(relative_path)
            if entry_pair is None:
                continue
            entry, permission = entry_pair
            tool_path = _tool_path(relative_path)
            if not _is_markdown_asset(tool_path):
                continue
            metadata = _entry_metadata(permission, entry, tool_path)
            metadata["kind"] = "asset"
            old_metadata = previous_files.get(tool_path) if isinstance(previous_files, dict) else None
            if _metadata_changed(old_metadata, metadata):
                content, truncated = await self._client.read_bytes(entry.path, max_bytes=self._sync.max_file_size_bytes)
                if truncated:
                    continue
                cache_path = self._cache_file_path(tool_path)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(content)
            metadata["cache_path"] = self._cache_file_path(tool_path).relative_to(self._cache_dir).as_posix()
            next_files[tool_path] = metadata
        _write_json(
            self._index_path,
            {
                "schema_version": 1,
                "updated_at": _now(),
                "files": next_files,
            },
        )

    def documents(self) -> list[WebDAVContextDocument]:
        index = _read_json(self._index_path)
        raw_files = index.get("files") if isinstance(index, dict) else {}
        if not isinstance(raw_files, dict):
            return []
        documents: list[WebDAVContextDocument] = []
        for tool_path, metadata in sorted(raw_files.items()):
            if not isinstance(tool_path, str) or not isinstance(metadata, dict):
                continue
            if metadata.get("kind", "document") != "document":
                continue
            cache_relative = str(metadata.get("cache_path") or "")
            if not cache_relative:
                continue
            cache_path = (self._cache_dir / cache_relative).resolve()
            try:
                if not cache_path.is_relative_to(self._cache_dir.resolve()) or not cache_path.is_file():
                    continue
                content = cache_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            documents.append(WebDAVContextDocument(path=tool_path, content=content))
        return documents

    async def write(self, *, absolute_path: str, content: str, mode: str = "append") -> dict[str, object]:
        relative = self._resolve_write_path(absolute_path)
        permission = _permission_for_path("/" + relative, self._context.webdav_permissions)
        if permission is None or permission.protected or not permission.writable:
            raise WebDAVContextError("webdav path is protected or not writable")
        write_mode = str(mode or "append").strip().lower()
        if write_mode not in {"append", "overwrite", "create"}:
            raise WebDAVContextError("mode must be append, overwrite, or create")
        if not content:
            raise WebDAVContextError("content is required")
        if len(content.encode("utf-8")) > self._sync.max_file_size_bytes:
            raise WebDAVContextError("content is too large")
        if PurePosixPath(relative).suffix.lower() not in set(self._sync.extensions):
            raise WebDAVContextError("absolute_path suffix is not allowed")

        remote_path = _join_remote(self._sync.root_path, relative)
        if write_mode == "append":
            try:
                existing, truncated = await self._client.read_bytes(remote_path, max_bytes=self._sync.max_file_size_bytes)
            except AgentConfigError:
                existing_text = ""
            else:
                existing_text = "" if truncated else existing.decode("utf-8", errors="replace")
            separator = "" if existing_text.endswith("\n") or not existing_text else "\n"
            next_content = existing_text + separator + content
        elif write_mode == "create":
            try:
                await self._client.read_bytes(remote_path, max_bytes=1)
            except AgentConfigError:
                next_content = content
            else:
                raise WebDAVContextError("file already exists")
        else:
            next_content = content

        await self._client.write_bytes(remote_path, next_content.encode("utf-8"), create_parent=True)
        tool_path = _tool_path(relative)
        cache_path = self._cache_file_path(tool_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(next_content, encoding="utf-8")
        self._update_cached_write(
            permission=permission,
            tool_path=tool_path,
            remote_path=remote_path,
            bytes_count=len(next_content.encode("utf-8")),
        )
        return {
            "type": "knowledge",
            "backend": "webdav",
            "path": tool_path,
            "mode": write_mode,
            "bytes": len(next_content.encode("utf-8")),
        }

    async def _scan_root(self) -> list[WebDAVEntry]:
        pending = [self._sync.root_path]
        files: list[WebDAVEntry] = []
        while pending and len(files) < self._sync.max_files_per_root:
            current = pending.pop(0)
            for entry in await self._client.list(current):
                if entry.is_dir:
                    pending.append(entry.path)
                else:
                    files.append(entry)
                    if len(files) >= self._sync.max_files_per_root:
                        break
        return files

    def _resolve_write_path(self, absolute_path: str) -> str:
        value = str(absolute_path or "").strip()
        if not value.startswith("/webdav/"):
            raise WebDAVContextError("webdav absolute_path must start with /webdav/")
        parts = PurePosixPath(value).parts
        if len(parts) < 3:
            raise WebDAVContextError("webdav absolute_path must include file path")
        relative_parts = parts[2:]
        if any(part in {"", ".", ".."} for part in relative_parts):
            raise WebDAVContextError("webdav absolute_path must not contain . or ..")
        return "/".join(relative_parts)

    def _is_stale(self) -> bool:
        index = _read_json(self._index_path)
        updated_at = str(index.get("updated_at") or "") if isinstance(index, dict) else ""
        if not updated_at:
            return True
        try:
            updated = datetime.fromisoformat(updated_at)
        except ValueError:
            return True
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        return age >= self._sync.interval_seconds

    def _cache_file_path(self, tool_path: str) -> Path:
        safe = tool_path.removeprefix("/").replace("/", "__")
        return self._files_dir / safe

    def _update_cached_write(self, *, permission: WebDAVPermission, tool_path: str, remote_path: str, bytes_count: int) -> None:
        index = _read_json(self._index_path)
        files = index.get("files") if isinstance(index, dict) else {}
        if not isinstance(files, dict):
            files = {}
        files[tool_path] = {
            "permission_path": permission.path,
            "remote_path": remote_path,
            "size": bytes_count,
            "modified": _now(),
            "etag": "",
            "protected": permission.protected,
            "writable": permission.writable,
            "cache_path": self._cache_file_path(tool_path).relative_to(self._cache_dir).as_posix(),
        }
        _write_json(self._index_path, {"schema_version": 1, "updated_at": _now(), "files": files})


def run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _entry_metadata(permission: WebDAVPermission, entry: WebDAVEntry, tool_path: str) -> dict[str, Any]:
    return {
        "permission_path": permission.path,
        "remote_path": entry.path,
        "tool_path": tool_path,
        "size": entry.size,
        "modified": entry.modified,
        "etag": entry.etag,
        "protected": permission.protected,
        "writable": permission.writable,
    }


def _metadata_changed(old: Any, new: dict[str, Any]) -> bool:
    if not isinstance(old, dict):
        return True
    for key in ("size", "modified", "etag", "remote_path"):
        if old.get(key) != new.get(key):
            return True
    return False


def _tool_path(relative_path: str) -> str:
    return f"/webdav/{relative_path.lstrip('/')}"


def _permission_for_path(path: str, permissions: tuple[WebDAVPermission, ...]) -> WebDAVPermission | None:
    normalized = _normalize_remote_path(path)
    matches = [permission for permission in permissions if _is_permission_match(normalized, permission.path)]
    if not matches:
        return None
    return max(matches, key=lambda permission: len(PurePosixPath(_normalize_remote_path(permission.path)).parts))


def _is_permission_match(path: str, permission_path: str) -> bool:
    normalized_permission = _normalize_remote_path(permission_path)
    if normalized_permission == "/":
        return True
    return path == normalized_permission or _is_child_path(path, normalized_permission)


def _relative_to_root(root_path: str, entry_path: str) -> str:
    root = PurePosixPath("/" + root_path.strip("/"))
    entry = PurePosixPath("/" + entry_path.strip("/"))
    try:
        relative = entry.relative_to(root)
    except ValueError:
        relative = PurePosixPath(entry.name)
    return relative.as_posix()


def _full_remote_root(nutstore_root_path: str, context_root_path: str) -> str:
    root = PurePosixPath("/" + str(nutstore_root_path or "/").strip("/"))
    if str(root) == ".":
        root = PurePosixPath("/")
    context = str(context_root_path or "/").strip("/")
    if root == PurePosixPath("/"):
        return "/" + context if context else "/"
    return "/" + (root / context).as_posix().strip("/")


def _is_child_path(path: str, parent: str) -> bool:
    path_value = PurePosixPath(_normalize_remote_path(path))
    parent_value = PurePosixPath(_normalize_remote_path(parent))
    try:
        path_value.relative_to(parent_value)
    except ValueError:
        return False
    return path_value != parent_value


def _normalize_remote_path(path: str) -> str:
    return "/" + str(PurePosixPath("/" + str(path or "/").strip("/"))).strip("/")


def _join_remote(root_path: str, relative_path: str) -> str:
    return "/" + (PurePosixPath(root_path.strip("/")) / relative_path).as_posix()


def _is_text_document(path: str, sync: WebDAVSyncConfig) -> bool:
    suffix = PurePosixPath(path).suffix.lower()
    return suffix in set(sync.extensions) and suffix in WebDAVContextService.text_suffixes


def _is_markdown_asset(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in WebDAVContextService.markdown_asset_suffixes


def _markdown_asset_references(content: str, document_relative_path: str) -> set[str]:
    document_parent = PurePosixPath(document_relative_path).parent
    references: set[str] = set()
    for raw_target in _markdown_image_targets(content):
        target = _normalize_markdown_asset_target(raw_target)
        if target is None:
            continue
        if target.startswith("/"):
            candidate = target.lstrip("/")
        else:
            candidate = (document_parent / target).as_posix()
        try:
            normalized = PurePosixPath(candidate)
            if any(part in {"", ".", ".."} for part in normalized.parts):
                continue
        except ValueError:
            continue
        if normalized.suffix.lower() in WebDAVContextService.markdown_asset_suffixes:
            references.add(normalized.as_posix())
    return references


def _markdown_image_targets(content: str) -> list[str]:
    targets = re.findall(r"!\[[^\]]*]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", content)
    targets.extend(re.findall(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", content, flags=re.IGNORECASE))
    return targets


def _normalize_markdown_asset_target(raw_target: str) -> str | None:
    value = raw_target.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or value.startswith(("#", "data:")):
        return None
    return unquote(parsed.path).strip()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
