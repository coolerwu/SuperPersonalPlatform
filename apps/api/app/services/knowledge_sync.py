"""Two-way sync between a WebDAV folder (Nutstore) and a local mirror.

The mirror is mounted as ``/knowledge/`` for Agents that enable the
``knowledge-base`` skill, so the Agent reads and writes it with the normal
file tools. A background task reconciles the mirror with WebDAV every few
minutes; credentials never leave this process.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.config import settings
from app.db import SessionLocal
from app.models import KnowledgeConfig

logger = logging.getLogger(__name__)

KNOWLEDGE_SKILL_NAME = "knowledge-base"
KNOWLEDGE_CONFIG_ID = "default"
STATE_VERSION = 1
CHUNK_SIZE = 1024 * 1024
MAX_REMOTE_DIRS = 5_000
MAX_REMOTE_FILES = 50_000
MIN_SYNC_INTERVAL_SECONDS = 30

DAV_NS = "{DAV:}"
PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getetag/><d:getcontentlength/><d:getlastmodified/>"
    "</d:prop></d:propfind>"
)


class WebDavError(RuntimeError):
    def __init__(self, status_code: int, path: str) -> None:
        self.status_code = status_code
        self.path = path
        super().__init__(f"WebDAV request failed with HTTP {status_code} for {path!r}")


@dataclass(frozen=True)
class RemoteEntry:
    path: str
    etag: str
    mtime: str
    size: int


@dataclass(frozen=True)
class LocalEntry:
    path: Path
    rel: str
    size: int
    digest: str


@dataclass
class SyncResult:
    downloaded: int = 0
    uploaded: int = 0
    conflicts: int = 0
    removed_local: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(frozen=True)
class KnowledgeSettings:
    enabled: bool
    webdav_url: str
    username: str
    password: str
    root: str
    interval_seconds: int

    @property
    def configured(self) -> bool:
        return bool(self.webdav_url.strip())

    @property
    def signature(self) -> tuple[str, str, str, str]:
        # interval_seconds is applied without rebuilding the WebDAV client.
        return (
            self.webdav_url.strip(),
            self.username.strip(),
            self.password,
            self.root.strip(),
        )


def _remote_signature(entry: RemoteEntry) -> tuple[str, str, int]:
    return (entry.etag, entry.mtime, entry.size)


def _state_signature(state: dict) -> tuple[str, str, int]:
    return (state.get("etag", ""), state.get("mtime", ""), int(state.get("size", 0) or 0))


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _clean_etag(value: str) -> str:
    value = value.strip()
    if value.startswith("W/"):
        value = value[2:]
    return value.strip().strip('"')


def _hash_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _settings_from_row(row: KnowledgeConfig | None) -> KnowledgeSettings | None:
    if row is None:
        return None
    return KnowledgeSettings(
        enabled=bool(row.enabled),
        webdav_url=(row.webdav_url or "").strip(),
        username=(row.username or "").strip(),
        password=row.password or "",
        root=(row.root or "").strip().strip("/"),
        interval_seconds=max(MIN_SYNC_INTERVAL_SECONDS, int(row.interval_seconds or 0)),
    )


async def load_knowledge_settings() -> KnowledgeSettings | None:
    async with SessionLocal() as db:
        row = await db.get(KnowledgeConfig, KNOWLEDGE_CONFIG_ID)
    return _settings_from_row(row)


async def seed_knowledge_config() -> None:
    """Adopt KB_* env vars once, until the config page has saved its own values."""
    if not settings.kb_webdav_url.strip():
        return
    async with SessionLocal() as db:
        if await db.get(KnowledgeConfig, KNOWLEDGE_CONFIG_ID) is not None:
            return
        db.add(
            KnowledgeConfig(
                id=KNOWLEDGE_CONFIG_ID,
                enabled=True,
                webdav_url=settings.kb_webdav_url.strip(),
                username=settings.kb_webdav_username.strip(),
                password=settings.kb_webdav_password,
                root=settings.kb_webdav_root.strip().strip("/"),
                interval_seconds=max(
                    MIN_SYNC_INTERVAL_SECONDS, settings.kb_sync_interval_seconds
                ),
            )
        )
        await db.commit()


class WebDavClient:
    """Minimal WebDAV client for the operations the mirror sync needs."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        root: str = "",
        timeout: float = 30,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.root = root.strip("/")
        self.listing_truncated = False
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout,
            auth=(username, password) if username else None,
            trust_env=True,
            follow_redirects=True,
            headers={"User-Agent": "AgentCommandCenter/0.1"},
        )
        base_path = unquote(urlsplit(self.base_url).path).rstrip("/")
        self._prefix = f"{base_path}/{self.root}".rstrip("/")

    async def close(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    def _url(self, rel: str) -> str:
        path = "/".join(part for part in (self.root, rel) if part)
        return self.base_url + quote(path, safe="/")

    def _relative(self, href: str) -> str | None:
        path = unquote(urlsplit(href).path).rstrip("/")
        if path == self._prefix:
            return ""
        prefix = f"{self._prefix}/"
        if not path.startswith(prefix):
            return None
        rel = path[len(prefix) :]
        if not rel or rel.startswith("/") or "\\" in rel:
            return None
        if any(part in ("", ".", "..") for part in rel.split("/")):
            return None
        return unicodedata.normalize("NFC", rel)

    async def _propfind(self, rel: str, depth: int) -> list[tuple[str, RemoteEntry, bool]]:
        url = self._url(rel)
        if depth == 1 and not url.endswith("/"):
            url += "/"
        response = await self._client.request(
            "PROPFIND",
            url,
            headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"},
            content=PROPFIND_BODY,
        )
        if response.status_code == 404:
            if not rel:
                # A missing root is a configuration error. Treating it as an
                # empty folder would make the mirror delete every tracked file.
                raise WebDavError(response.status_code, "/")
            return []
        if response.status_code not in (200, 207):
            raise WebDavError(response.status_code, rel or "/")
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise WebDavError(502, rel or "/") from exc
        entries: list[tuple[str, RemoteEntry, bool]] = []
        for node in root.findall(f"{DAV_NS}response"):
            href = node.findtext(f"{DAV_NS}href") or ""
            path = self._relative(href)
            if path is None:
                continue
            props = node.findall(f"{DAV_NS}propstat/{DAV_NS}prop")
            if not props:
                continue
            is_dir = False
            etag = ""
            mtime = ""
            size = 0
            for prop in props:
                resource_type = prop.find(f"{DAV_NS}resourcetype")
                if (
                    resource_type is not None
                    and resource_type.find(f"{DAV_NS}collection") is not None
                ):
                    is_dir = True
                if not etag:
                    etag = _clean_etag(prop.findtext(f"{DAV_NS}getetag") or "")
                if not mtime:
                    mtime = (prop.findtext(f"{DAV_NS}getlastmodified") or "").strip()
                if not size:
                    try:
                        size = int(prop.findtext(f"{DAV_NS}getcontentlength") or 0)
                    except ValueError:
                        size = 0
            entries.append((path, RemoteEntry(path, etag, mtime, size), is_dir))
        if not rel and not entries:
            # A 207 response with no recognizable hrefs means the listing is
            # malformed, not that the folder is empty.
            raise WebDavError(502, "/")
        return entries

    async def list_files(self) -> dict[str, RemoteEntry]:
        files: dict[str, RemoteEntry] = {}
        pending = [""]
        seen: set[str] = set()
        self.listing_truncated = False
        while pending:
            rel_dir = pending.pop()
            if rel_dir in seen:
                continue
            if len(seen) >= MAX_REMOTE_DIRS:
                logger.warning("knowledge sync: stopped after %d directories", MAX_REMOTE_DIRS)
                self.listing_truncated = True
                break
            seen.add(rel_dir)
            for rel, entry, is_dir in await self._propfind(rel_dir, 1):
                if is_dir:
                    pending.append(rel)
                else:
                    files[rel] = entry
                if len(files) >= MAX_REMOTE_FILES:
                    logger.warning("knowledge sync: stopped after %d files", MAX_REMOTE_FILES)
                    self.listing_truncated = True
                    return files
        return files

    async def get(self, rel: str) -> bytes:
        response = await self._client.get(self._url(rel))
        if response.status_code != 200:
            raise WebDavError(response.status_code, rel)
        return response.content

    async def put(self, rel: str, data: bytes) -> RemoteEntry | None:
        await self.ensure_dirs(rel)
        response = await self._client.put(
            self._url(rel),
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        if response.status_code not in (200, 201, 204):
            raise WebDavError(response.status_code, rel)
        etag = _clean_etag(response.headers.get("etag") or "")
        if not etag:
            return None
        return RemoteEntry(rel, etag, response.headers.get("last-modified", ""), len(data))

    async def stat(self, rel: str) -> RemoteEntry | None:
        for path, entry, is_dir in await self._propfind(rel, 0):
            if path == rel and not is_dir:
                return entry
        return None

    async def mkcol(self, rel: str) -> None:
        if not rel:
            return
        response = await self._client.request("MKCOL", self._url(rel))
        if response.status_code not in (200, 201, 204, 405):
            raise WebDavError(response.status_code, rel)

    async def ensure_dirs(self, rel: str) -> None:
        parts = rel.split("/")[:-1]
        for index in range(1, len(parts) + 1):
            await self.mkcol("/".join(parts[:index]))


class KnowledgeSync:
    def __init__(
        self,
        *,
        client: WebDavClient,
        local_dir: Path,
        state_path: Path,
        max_file_bytes: int | None = None,
    ) -> None:
        self.client = client
        self.local_dir = Path(local_dir).expanduser()
        self.state_path = Path(state_path)
        self.max_file_bytes = (
            settings.kb_max_file_bytes if max_file_bytes is None else max_file_bytes
        )
        self._lock = asyncio.Lock()

    async def sync_once(self) -> SyncResult:
        async with self._lock:
            return await self._sync_once()

    async def _sync_once(self) -> SyncResult:
        result = SyncResult()
        self.local_dir.mkdir(parents=True, exist_ok=True)
        previous = self._load_state()

        try:
            remote = await self.client.list_files()
        except WebDavError as exc:
            logger.warning("knowledge sync: cannot list WebDAV folder: %s", exc)
            if exc.status_code in (401, 403):
                raise RuntimeError("WebDAV 认证失败，请检查账号和应用密码") from exc
            raise RuntimeError(f"无法访问 WebDAV（HTTP {exc.status_code}）") from exc
        except Exception as exc:
            logger.warning("knowledge sync: cannot list WebDAV folder: %s", exc)
            detail = str(exc).strip() or type(exc).__name__
            raise RuntimeError(f"无法访问 WebDAV：{detail}") from exc

        local = self._scan_local(previous)
        next_state: dict[str, dict] = {}

        for rel in sorted(remote):
            entry = remote[rel]
            prev = previous.get(rel)
            local_entry = local.get(rel)
            remote_changed = prev is None or _remote_signature(entry) != _state_signature(prev)
            local_changed = local_entry is not None and (
                prev is None or local_entry.digest != prev.get("local_hash", "")
            )

            if entry.size > self.max_file_bytes:
                logger.warning(
                    "knowledge sync: skipping oversized file %s (%d bytes)", rel, entry.size
                )
                result.skipped += 1
                if prev is not None:
                    next_state[rel] = prev
                continue

            if local_entry is None:
                state = await self._download(rel, entry, result)
                if state is not None:
                    next_state[rel] = state
                elif prev is not None:
                    next_state[rel] = prev
                continue

            if not remote_changed:
                if not local_changed:
                    state = self._local_state(rel, entry, digest=local_entry.digest)
                    if state is not None:
                        next_state[rel] = state
                    elif prev is not None:
                        next_state[rel] = prev
                else:
                    state = await self._upload(local_entry, result)
                    if state is not None:
                        next_state[rel] = state
                    elif prev is not None:
                        next_state[rel] = prev
                continue

            if not local_changed:
                state = await self._download(rel, entry, result)
                if state is not None:
                    next_state[rel] = state
                elif prev is not None:
                    next_state[rel] = prev
                continue

            # Both sides changed. Without a baseline, identical content is not
            # a conflict; otherwise the remote copy wins the original name and
            # the local version is preserved beside it. Fetch before making
            # the conflict copy so a failed read cannot leave duplicates.
            payload: bytes | None = None
            if prev is None:
                try:
                    payload = await self.client.get(rel)
                except Exception as exc:
                    logger.warning("knowledge sync: cannot read %s: %s", rel, exc)
                    result.errors += 1
                    continue
                if _md5(payload) == local_entry.digest:
                    self._write_local(rel, payload)
                    state = self._local_state(rel, entry, digest=local_entry.digest)
                    if state is not None:
                        next_state[rel] = state
                    continue
            if payload is None:
                try:
                    payload = await self.client.get(rel)
                except Exception as exc:
                    logger.warning("knowledge sync: cannot read %s: %s", rel, exc)
                    result.errors += 1
                    if prev is not None:
                        next_state[rel] = prev
                    continue
            if not self._save_conflict_copy(local_entry):
                result.errors += 1
                if prev is not None:
                    next_state[rel] = prev
                continue
            result.conflicts += 1
            if not self._write_local(rel, payload):
                result.errors += 1
                if prev is not None:
                    next_state[rel] = prev
                continue
            result.downloaded += 1
            state = self._local_state(rel, entry, digest=_md5(payload))
            if state is not None:
                next_state[rel] = state
            elif prev is not None:
                next_state[rel] = prev

        if getattr(self.client, "listing_truncated", False):
            # Missing files in a truncated listing are not deletions. Keep the
            # previous baselines so the next complete listing can reconcile.
            logger.warning("knowledge sync: remote listing was truncated; skipping local pass")
            for rel, prev in previous.items():
                next_state.setdefault(rel, prev)
        else:
            # Local-only files: new notes (including conflict copies) are
            # uploaded; a file the remote deleted is removed locally only if
            # it is unmodified.
            local = self._scan_local(previous)
            for rel in sorted(local):
                if rel in remote or rel in next_state:
                    continue
                entry = local[rel]
                prev = previous.get(rel)
                if prev is not None and entry.digest == prev.get("local_hash", ""):
                    try:
                        entry.path.unlink()
                        result.removed_local += 1
                    except OSError as exc:
                        logger.warning("knowledge sync: cannot remove %s: %s", rel, exc)
                        result.errors += 1
                    continue
                if entry.size > self.max_file_bytes:
                    logger.warning(
                        "knowledge sync: skipping oversized file %s (%d bytes)",
                        rel,
                        entry.size,
                    )
                    result.skipped += 1
                    continue
                state = await self._upload(entry, result)
                if state is not None:
                    next_state[rel] = state

        self._save_state(next_state)
        return result

    def _local_path(self, rel: str) -> Path | None:
        if not rel or rel.startswith("/") or "\\" in rel:
            return None
        rel = unicodedata.normalize("NFC", rel)
        parts = PurePosixPath(rel).parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            return None
        root = self.local_dir.resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            return None
        return target

    def _scan_local(self, previous: dict[str, dict]) -> dict[str, LocalEntry]:
        entries: dict[str, LocalEntry] = {}
        root = self.local_dir
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                name for name in dirnames if not (Path(dirpath) / name).is_symlink()
            )
            for name in filenames:
                if name.endswith(".kb-tmp"):
                    continue
                path = Path(dirpath) / name
                if path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rel = unicodedata.normalize(
                    "NFC", path.relative_to(root).as_posix()
                )
                prev = previous.get(rel)
                cached = (
                    prev is not None
                    and prev.get("local_size") == stat.st_size
                    and prev.get("local_mtime_ns") == stat.st_mtime_ns
                    and prev.get("local_hash")
                )
                if cached:
                    digest = str(prev["local_hash"])
                else:
                    try:
                        digest = _hash_file(path)
                    except OSError as exc:
                        logger.warning("knowledge sync: cannot hash %s: %s", rel, exc)
                        continue
                entries[rel] = LocalEntry(path, rel, stat.st_size, digest)
        return entries

    def _local_state(self, rel: str, entry: RemoteEntry, *, digest: str | None) -> dict | None:
        target = self._local_path(rel)
        if target is None or not target.is_file():
            return None
        try:
            stat = target.stat()
            if digest is None:
                digest = _hash_file(target)
        except OSError as exc:
            logger.warning("knowledge sync: cannot stat %s: %s", rel, exc)
            return None
        return {
            "etag": entry.etag,
            "mtime": entry.mtime,
            "size": entry.size,
            "local_hash": digest,
            "local_size": stat.st_size,
            "local_mtime_ns": stat.st_mtime_ns,
        }

    async def _download(self, rel: str, entry: RemoteEntry, result: SyncResult) -> dict | None:
        target = self._local_path(rel)
        if target is None:
            logger.warning("knowledge sync: refusing unsafe path %s", rel)
            result.errors += 1
            return None
        if target.is_symlink():
            logger.warning("knowledge sync: refusing to write through symlink %s", rel)
            result.errors += 1
            return None
        try:
            payload = await self.client.get(rel)
        except Exception as exc:
            logger.warning("knowledge sync: cannot download %s: %s", rel, exc)
            result.errors += 1
            return None
        if len(payload) > self.max_file_bytes:
            logger.warning(
                "knowledge sync: skipping oversized download %s (%d bytes)", rel, len(payload)
            )
            result.skipped += 1
            return None
        if not self._write_local(rel, payload):
            result.errors += 1
            return None
        result.downloaded += 1
        return self._local_state(rel, entry, digest=_md5(payload))

    def _write_local(self, rel: str, payload: bytes) -> bool:
        target = self._local_path(rel)
        if target is None:
            return False
        temp = target.with_name(f".{target.name}.kb-tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp.write_bytes(payload)
            os.replace(temp, target)
        except OSError as exc:
            logger.warning("knowledge sync: cannot write %s: %s", rel, exc)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    async def _upload(self, entry: LocalEntry, result: SyncResult) -> dict | None:
        if entry.size > self.max_file_bytes:
            logger.warning(
                "knowledge sync: skipping oversized upload %s (%d bytes)",
                entry.rel,
                entry.size,
            )
            result.skipped += 1
            return None
        try:
            payload = entry.path.read_bytes()
        except OSError as exc:
            logger.warning("knowledge sync: cannot read %s: %s", entry.rel, exc)
            result.errors += 1
            return None
        try:
            remote_entry = await self.client.put(entry.rel, payload)
            if remote_entry is None:
                remote_entry = await self.client.stat(entry.rel)
        except Exception as exc:
            logger.warning("knowledge sync: cannot upload %s: %s", entry.rel, exc)
            result.errors += 1
            return None
        result.uploaded += 1
        if remote_entry is None:
            remote_entry = RemoteEntry(entry.rel, "", "", len(payload))
        return self._local_state(entry.rel, remote_entry, digest=entry.digest)

    def _save_conflict_copy(self, entry: LocalEntry) -> bool:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        base = entry.path
        target = base.with_name(f"{base.stem}.conflict-local-{stamp}{base.suffix}")
        counter = 1
        while target.exists():
            target = base.with_name(
                f"{base.stem}.conflict-local-{stamp}-{counter}{base.suffix}"
            )
            counter += 1
        try:
            shutil.copy2(base, target)
        except OSError as exc:
            logger.warning("knowledge sync: cannot keep conflict copy for %s: %s", entry.rel, exc)
            return False
        return True

    def _load_state(self) -> dict[str, dict]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, dict):
            return {}
        state: dict[str, dict] = {}
        for rel, entry in files.items():
            if not isinstance(rel, str) or not isinstance(entry, dict):
                continue
            try:
                size = int(entry.get("size") or 0)
                local_size = int(entry.get("local_size") or 0)
                local_mtime_ns = int(entry.get("local_mtime_ns") or 0)
            except (TypeError, ValueError):
                continue
            state[rel] = {
                "etag": str(entry.get("etag") or ""),
                "mtime": str(entry.get("mtime") or ""),
                "size": size,
                "local_hash": str(entry.get("local_hash") or ""),
                "local_size": local_size,
                "local_mtime_ns": local_mtime_ns,
            }
        return state

    def _save_state(self, files: dict[str, dict]) -> None:
        payload = json.dumps(
            {"version": STATE_VERSION, "files": dict(sorted(files.items()))},
            ensure_ascii=False,
            indent=2,
        )
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_name(f"{self.state_path.name}.tmp")
            temp.write_text(payload + "\n", encoding="utf-8")
            os.replace(temp, self.state_path)
        except OSError as exc:
            logger.warning("knowledge sync: cannot save sync state: %s", exc)


def _make_webdav_client(config: KnowledgeSettings) -> WebDavClient:
    return WebDavClient(
        config.webdav_url,
        config.username,
        config.password,
        root=config.root,
        timeout=settings.kb_sync_timeout_seconds,
    )


class KnowledgeSyncRuntime:
    """Loads the knowledge configuration and runs the mirror sync in-process.

    The config lives in app.db and is managed from the config page. It is
    re-read before every cycle, so saving the page takes effect on the next
    wake without restarting the service.
    """

    def __init__(
        self,
        *,
        config_loader: Callable[[], Awaitable[KnowledgeSettings | None]] | None = None,
        client_factory: Callable[[KnowledgeSettings], Any] | None = None,
        local_dir: Path | None = None,
        state_path: Path | None = None,
        interval_seconds: int | None = None,
    ) -> None:
        self._config_loader = config_loader or load_knowledge_settings
        self._client_factory = client_factory or _make_webdav_client
        self.local_dir = Path(local_dir or settings.knowledge_dir).expanduser()
        self.state_path = Path(state_path or settings.knowledge_state_path)
        self._default_interval = max(
            MIN_SYNC_INTERVAL_SECONDS,
            interval_seconds if interval_seconds is not None else settings.kb_sync_interval_seconds,
        )
        self._interval = self._default_interval
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._client: Any = None
        self._signature: tuple[str, str, str, str] | None = None
        self.sync: KnowledgeSync | None = None
        self.enabled = False
        self.configured = False
        self.last_sync_at: str | None = None
        self.last_result: SyncResult | None = None
        self.last_error = ""

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="knowledge-sync")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._close_client()

    def wake(self) -> None:
        self._wake.set()

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled and self.configured,
            "configured": self.configured,
            "local_dir": str(self.local_dir),
            "interval_seconds": self._interval,
            "last_sync_at": self.last_sync_at,
            "last_error": self.last_error,
            "last_result": asdict(self.last_result) if self.last_result is not None else None,
        }

    async def _close_client(self) -> None:
        client = self._client
        self._client = None
        self.sync = None
        self._signature = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is None:
            return
        try:
            await close()
        except Exception:
            logger.warning("knowledge sync: failed to close WebDAV client", exc_info=True)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            interval = self._interval
            try:
                config = await self._config_loader()
                if config is None or not config.configured:
                    self.configured = False
                    self.enabled = False
                    self.last_error = ""
                    await self._close_client()
                elif not config.enabled:
                    self.configured = True
                    self.enabled = False
                    self.last_error = ""
                    await self._close_client()
                else:
                    self.configured = True
                    self.enabled = True
                    if self.sync is None or config.signature != self._signature:
                        await self._close_client()
                        self._client = self._client_factory(config)
                        self.sync = KnowledgeSync(
                            client=self._client,
                            local_dir=self.local_dir,
                            state_path=self.state_path,
                        )
                        self._signature = config.signature
                    self._interval = max(
                        MIN_SYNC_INTERVAL_SECONDS, config.interval_seconds
                    )
                    interval = self._interval
                    result = await self.sync.sync_once()
                    self.last_sync_at = datetime.now(timezone.utc).isoformat()
                    self.last_result = result
                    self.last_error = "" if result.errors == 0 else "同步完成，但有文件失败"
                    if (
                        result.downloaded
                        or result.uploaded
                        or result.conflicts
                        or result.removed_local
                    ):
                        logger.info(
                            "knowledge sync: downloaded=%d uploaded=%d conflicts=%d removed=%d",
                            result.downloaded,
                            result.uploaded,
                            result.conflicts,
                            result.removed_local,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_sync_at = datetime.now(timezone.utc).isoformat()
                self.last_error = str(exc) or type(exc).__name__
                logger.warning("knowledge sync failed: %s", exc)
            if self._stop.is_set():
                break
            elapsed = time.monotonic() - started
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(1.0, interval - elapsed))
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
