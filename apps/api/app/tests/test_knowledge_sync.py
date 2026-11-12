from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.services.knowledge_sync import (
    KnowledgeSettings,
    KnowledgeSync,
    KnowledgeSyncRuntime,
    RemoteEntry,
    WebDavClient,
    WebDavError,
    WebDavUnavailable,
)


class FakeDav:
    """In-memory WebDAV folder used by the sync tests."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.uploads: list[str] = []
        self.deletes: list[str] = []
        self.fail_gets: set[str] = set()
        self.fail_deletes: set[str] = set()
        self.listing_incomplete = False

    def _etag(self, rel: str) -> str:
        return hashlib.md5(self.files[rel]).hexdigest()

    async def list_files(self) -> dict[str, RemoteEntry]:
        return {
            rel: RemoteEntry(rel, self._etag(rel), "", len(data))
            for rel, data in self.files.items()
        }

    async def get(self, rel: str) -> bytes:
        if rel in self.fail_gets:
            raise WebDavError(500, rel)
        return self.files[rel]

    async def put(self, rel: str, data: bytes) -> RemoteEntry:
        self.files[rel] = data
        self.uploads.append(rel)
        return RemoteEntry(rel, self._etag(rel), "", len(data))

    async def delete(self, rel: str) -> None:
        if rel in self.fail_deletes:
            raise WebDavError(500, rel)
        del self.files[rel]
        self.deletes.append(rel)

    async def stat(self, rel: str) -> RemoteEntry | None:
        if rel not in self.files:
            return None
        return RemoteEntry(rel, self._etag(rel), "", len(self.files[rel]))

    async def ensure_dirs(self, rel: str) -> None:
        return None

    async def close(self) -> None:
        return None


def make_sync(tmp_path: Path, files: dict[str, bytes] | None = None) -> tuple[KnowledgeSync, FakeDav, Path]:
    dav = FakeDav(files)
    local = tmp_path / "knowledge"
    sync = KnowledgeSync(
        client=dav,
        local_dir=local,
        state_path=tmp_path / "knowledge_state.json",
    )
    return sync, dav, local


async def test_initial_sync_pulls_remote_files(tmp_path: Path) -> None:
    sync, _, local = make_sync(tmp_path, {"笔记/a.md": "hello".encode(), "b.md": "山".encode()})

    result = await sync.sync_once()

    assert result.downloaded == 2
    assert (local / "笔记" / "a.md").read_text(encoding="utf-8") == "hello"
    assert (local / "b.md").read_text(encoding="utf-8") == "山"

    again = await sync.sync_once()
    assert again.downloaded == 0
    assert again.uploaded == 0


async def test_new_local_file_is_uploaded(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path)
    await sync.sync_once()

    (local / "note.md").write_text("# 新笔记", encoding="utf-8")
    result = await sync.sync_once()

    assert result.uploaded == 1
    assert dav.files["note.md"].decode("utf-8") == "# 新笔记"


async def test_local_edit_is_uploaded(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"old"})
    await sync.sync_once()

    (local / "note.md").write_text("new", encoding="utf-8")
    result = await sync.sync_once()

    assert result.uploaded == 1
    assert dav.files["note.md"] == b"new"


async def test_remote_edit_is_downloaded(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"old"})
    await sync.sync_once()

    dav.files["note.md"] = b"remote changed"
    result = await sync.sync_once()

    assert result.downloaded == 1
    assert (local / "note.md").read_bytes() == b"remote changed"


async def test_conflict_keeps_local_copy_and_remote_wins(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"base"})
    await sync.sync_once()

    (local / "note.md").write_text("local edit", encoding="utf-8")
    dav.files["note.md"] = b"remote edit"
    result = await sync.sync_once()

    assert result.conflicts == 1
    assert (local / "note.md").read_bytes() == b"remote edit"
    copies = list(local.glob("note.conflict-local-*.md"))
    assert len(copies) == 1
    assert copies[0].read_text(encoding="utf-8") == "local edit"
    assert copies[0].name in dav.files
    assert dav.files[copies[0].name] == b"local edit"


async def test_remote_delete_removes_unmodified_local(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"gone.md": b"bye"})
    await sync.sync_once()

    del dav.files["gone.md"]
    result = await sync.sync_once()

    assert result.removed_local == 1
    assert not (local / "gone.md").exists()


async def test_remote_delete_keeps_modified_local(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"keep.md": b"base"})
    await sync.sync_once()

    del dav.files["keep.md"]
    (local / "keep.md").write_text("edited locally", encoding="utf-8")
    result = await sync.sync_once()

    assert result.uploaded == 1
    assert dav.files["keep.md"] == b"edited locally"


async def test_local_delete_is_propagated_after_confirmation(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"content"})
    await sync.sync_once()

    (local / "note.md").unlink()
    armed = await sync.sync_once()
    assert armed.removed_remote == 0
    assert "note.md" in dav.files

    confirmed = await sync.sync_once()
    assert confirmed.removed_remote == 1
    assert "note.md" not in dav.files
    assert not (local / "note.md").exists()


async def test_local_move_replaces_the_remote_path(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"00Inbox/note.md": "content".encode()})
    await sync.sync_once()

    (local / "00Inbox" / "note.md").unlink()
    (local / "01Cards").mkdir()
    (local / "01Cards" / "note.md").write_bytes("content".encode())

    await sync.sync_once()
    assert dav.files["01Cards/note.md"] == "content".encode()
    assert "00Inbox/note.md" in dav.files

    await sync.sync_once()
    assert "00Inbox/note.md" not in dav.files
    assert dav.files["01Cards/note.md"] == "content".encode()


async def test_remote_edit_wins_over_a_local_deletion(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"base"})
    await sync.sync_once()

    (local / "note.md").unlink()
    dav.files["note.md"] = b"remote moved on"
    result = await sync.sync_once()

    assert result.removed_remote == 0
    assert dav.files["note.md"] == b"remote moved on"
    assert (local / "note.md").read_bytes() == b"remote moved on"


async def test_armed_deletion_is_cancelled_by_a_remote_edit(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"base"})
    await sync.sync_once()

    (local / "note.md").unlink()
    await sync.sync_once()
    assert "note.md" in dav.files

    dav.files["note.md"] = b"newer remote"
    result = await sync.sync_once()

    assert result.removed_remote == 0
    assert dav.files["note.md"] == b"newer remote"
    assert (local / "note.md").read_bytes() == b"newer remote"


async def test_returning_local_file_cancels_an_armed_deletion(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"content"})
    await sync.sync_once()

    (local / "note.md").unlink()
    await sync.sync_once()
    (local / "note.md").write_bytes(b"content")

    result = await sync.sync_once()
    assert result.removed_remote == 0
    assert "note.md" in dav.files

    (local / "note.md").unlink()
    await sync.sync_once()
    assert "note.md" in dav.files

    confirmed = await sync.sync_once()
    assert confirmed.removed_remote == 1
    assert "note.md" not in dav.files


async def test_bulk_local_delete_is_restored_instead_of_propagated(tmp_path: Path) -> None:
    files = {f"note-{index}.md": f"content {index}".encode() for index in range(24)}
    sync, dav, local = make_sync(tmp_path, files)
    await sync.sync_once()

    for name in files:
        (local / name).unlink()
    await sync.sync_once()
    result = await sync.sync_once()

    assert result.removed_remote == 0
    assert dav.deletes == []
    assert set(dav.files) == set(files)
    assert all((local / name).exists() for name in files)
    assert result.downloaded == len(files)


async def test_failed_remote_delete_retries_on_the_next_sync(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"content"})
    await sync.sync_once()

    (local / "note.md").unlink()
    await sync.sync_once()

    dav.fail_deletes.add("note.md")
    failed = await sync.sync_once()
    assert failed.errors == 1
    assert failed.removed_remote == 0
    assert "note.md" in dav.files

    dav.fail_deletes.clear()
    retried = await sync.sync_once()
    assert retried.removed_remote == 1
    assert "note.md" not in dav.files


async def test_failed_download_is_retried_instead_of_read_as_a_deletion(
    tmp_path: Path,
) -> None:
    sync, dav, local = make_sync(tmp_path, {"note.md": b"content"})
    dav.fail_gets.add("note.md")

    failed = await sync.sync_once()
    assert failed.errors == 1
    assert "note.md" in dav.files

    dav.fail_gets.clear()
    downloaded = await sync.sync_once()
    assert downloaded.downloaded == 1
    assert (local / "note.md").read_bytes() == b"content"

    settled = await sync.sync_once()
    assert settled.removed_remote == 0
    assert "note.md" in dav.files


async def test_unsafe_remote_path_is_skipped(tmp_path: Path) -> None:
    sync, _, local = make_sync(tmp_path, {"../escape.md": b"nope", "ok.md": b"fine"})
    result = await sync.sync_once()

    assert result.errors == 1
    assert result.downloaded == 1
    assert not (tmp_path / "escape.md").exists()
    assert (local / "ok.md").read_text(encoding="utf-8") == "fine"


async def test_webdav_auth_failure_is_readable(tmp_path: Path) -> None:
    class FailingDav(FakeDav):
        async def list_files(self) -> dict[str, RemoteEntry]:
            raise WebDavError(401, "/")

    sync = KnowledgeSync(
        client=FailingDav(),
        local_dir=tmp_path / "knowledge",
        state_path=tmp_path / "state.json",
    )
    with pytest.raises(RuntimeError, match="认证失败"):
        await sync.sync_once()


async def test_truncated_listing_does_not_delete_local(tmp_path: Path) -> None:
    sync, dav, local = make_sync(tmp_path, {"keep.md": b"content"})
    await sync.sync_once()

    dav.files.clear()
    dav.listing_incomplete = True
    result = await sync.sync_once()

    assert result.removed_local == 0
    assert (local / "keep.md").exists()

    dav.listing_incomplete = False
    result = await sync.sync_once()
    assert result.removed_local == 1
    assert not (local / "keep.md").exists()


async def test_runtime_is_disabled_without_config(tmp_path: Path) -> None:
    async def no_config() -> None:
        return None

    runtime = KnowledgeSyncRuntime(
        config_loader=no_config,
        local_dir=tmp_path / "knowledge",
        state_path=tmp_path / "state.json",
    )
    assert runtime.sync is None
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()
    assert runtime.status()["enabled"] is False
    assert runtime.status()["configured"] is False


async def test_runtime_syncs_loaded_config(tmp_path: Path) -> None:
    dav = FakeDav({"note.md": b"hello"})
    config = KnowledgeSettings(
        enabled=True,
        webdav_url="https://dav.example.com/dav/",
        username="user",
        password="pass",
        root="",
        interval_seconds=30,
    )

    async def loader() -> KnowledgeSettings:
        return config

    runtime = KnowledgeSyncRuntime(
        config_loader=loader,
        client_factory=lambda settings: dav,
        local_dir=tmp_path / "knowledge",
        state_path=tmp_path / "state.json",
    )
    await runtime.start()
    for _ in range(50):
        if runtime.last_sync_at:
            break
        await asyncio.sleep(0.02)
    await runtime.stop()

    assert (tmp_path / "knowledge" / "note.md").read_bytes() == b"hello"
    assert runtime.status()["enabled"] is True
    assert runtime.status()["configured"] is True
    assert runtime.last_result is not None


async def test_runtime_respects_disabled_config(tmp_path: Path) -> None:
    dav = FakeDav({"note.md": b"hello"})
    config = KnowledgeSettings(
        enabled=False,
        webdav_url="https://dav.example.com/dav/",
        username="user",
        password="pass",
        root="",
        interval_seconds=30,
    )

    async def loader() -> KnowledgeSettings:
        return config

    runtime = KnowledgeSyncRuntime(
        config_loader=loader,
        client_factory=lambda settings: dav,
        local_dir=tmp_path / "knowledge",
        state_path=tmp_path / "state.json",
    )
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()

    assert runtime.status()["enabled"] is False
    assert runtime.status()["configured"] is True
    assert not (tmp_path / "knowledge" / "note.md").exists()


MULTISTATUS = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/%E7%9F%A5%E8%AF%86%E5%BA%93/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/%E7%9F%A5%E8%AF%86%E5%BA%93/sub/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/%E7%9F%A5%E8%AF%86%E5%BA%93/sub/%E7%AC%94%E8%AE%B0.md</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getetag>"abc123"</d:getetag>
      <d:getcontentlength>12</d:getcontentlength>
      <d:getlastmodified>Sun, 02 Nov 2026 10:00:00 GMT</d:getlastmodified>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>
"""


async def test_webdav_client_lists_chinese_paths() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(207, text=MULTISTATUS)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=("user", "pass"))
    client = WebDavClient(
        "https://dav.example.com/dav/",
        "user",
        "pass",
        root="知识库",
        http_client=http,
        request_interval=0,
    )

    files = await client.list_files()

    assert set(files) == {"sub/笔记.md"}
    assert files["sub/笔记.md"].etag == "abc123"
    assert files["sub/笔记.md"].size == 12
    assert requests[0].method == "PROPFIND"
    assert requests[0].headers["depth"] == "1"
    assert requests[0].headers["authorization"].startswith("Basic ")
    await http.aclose()


async def test_webdav_client_rejects_missing_root() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WebDavClient(
        "https://dav.example.com/dav/",
        "user",
        "pass",
        root="missing",
        http_client=http,
        request_interval=0,
    )

    with pytest.raises(WebDavError):
        await client.list_files()
    await http.aclose()


async def test_webdav_client_writes_chinese_paths() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(201, headers={"ETag": '"new-etag"'})
        return httpx.Response(200, content=b"payload")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WebDavClient(
        "https://dav.example.com/dav/",
        "user",
        "pass",
        root="知识库",
        http_client=http,
        request_interval=0,
    )

    entry = await client.put("笔记/new.md", b"data")
    assert entry is not None
    assert entry.etag == "new-etag"
    assert any(
        "/dav/%E7%9F%A5%E8%AF%86%E5%BA%93/%E7%AC%94%E8%AE%B0/new.md" in str(request.url)
        for request in seen
    )
    assert await client.get("笔记/new.md") == b"payload"
    await http.aclose()


async def test_webdav_client_deletes_and_tolerates_missing_files() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "gone.md" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(204)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WebDavClient(
        "https://dav.example.com/dav/",
        "user",
        "pass",
        root="知识库",
        http_client=http,
        request_interval=0,
    )

    await client.delete("笔记/旧.md")
    await client.delete("gone.md")

    assert [request.method for request in requests] == ["DELETE", "DELETE"]
    assert "/dav/%E7%9F%A5%E8%AF%86%E5%BA%93/%E7%AC%94%E8%AE%B0/%E6%97%A7.md" in str(
        requests[0].url
    )
    await http.aclose()


async def test_webdav_client_flags_entry_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, text=MULTISTATUS)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WebDavClient(
        "https://dav.example.com/dav/",
        "user",
        "pass",
        root="知识库",
        http_client=http,
        request_interval=0,
        max_listing_entries=3,
    )

    files = await client.list_files()

    assert set(files) == {"sub/笔记.md"}
    assert client.listing_incomplete is True
    await http.aclose()


async def test_webdav_client_detects_temporary_block() -> None:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d:error xmlns:d="DAV:" xmlns:s="http://ns.jianguoyun.com">'
        "<s:exception>BlockedTemporarily</s:exception>"
        "<s:message>Too many requests are received recently</s:message>"
        "</d:error>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WebDavClient(
        "https://dav.example.com/dav/",
        "user",
        "pass",
        root="知识库",
        http_client=http,
        request_interval=0,
    )

    with pytest.raises(WebDavUnavailable) as info:
        await client.list_files()
    assert info.value.blocked is True
    assert info.value.retry_after_seconds >= 60
    await http.aclose()


async def test_webdav_client_excludes_config_dirs() -> None:
    client = WebDavClient(
        "https://dav.example.com/dav/",
        "user",
        "pass",
        exclude_dirs=".obsidian,.git",
        request_interval=0,
    )
    try:
        assert client._excluded(".obsidian/app.json")
        assert client._excluded("notes/.git/config")
        assert not client._excluded("notes/a.md")
    finally:
        await client.close()


async def test_runtime_pauses_after_temporary_block(tmp_path: Path) -> None:
    class BlockedDav(FakeDav):
        async def list_files(self) -> dict[str, RemoteEntry]:
            raise WebDavUnavailable(503, "/", blocked=True, retry_after_seconds=60)

    async def loader() -> KnowledgeSettings:
        return KnowledgeSettings(
            enabled=True,
            webdav_url="https://dav.example.com/dav/",
            username="user",
            password="pass",
            root="",
            interval_seconds=30,
        )

    runtime = KnowledgeSyncRuntime(
        config_loader=loader,
        client_factory=lambda settings: BlockedDav(),
        local_dir=tmp_path / "knowledge",
        state_path=tmp_path / "state.json",
    )
    await runtime.start()
    for _ in range(50):
        if runtime.status()["blocked"]:
            break
        await asyncio.sleep(0.02)
    await runtime.stop()

    assert runtime.status()["blocked"] is True
    assert str(runtime.status()["retry_at"]).startswith("20")
    assert "限制" in runtime.last_error


async def test_runtime_resets_mirror_when_remote_changes(tmp_path: Path) -> None:
    local = tmp_path / "knowledge"
    local.mkdir()
    (local / "old.md").write_text("old", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"version": 1, "remote_key": "old-key", "files": {}}),
        encoding="utf-8",
    )
    dav = FakeDav({"new.md": b"new"})
    config = KnowledgeSettings(
        enabled=True,
        webdav_url="https://dav.example.com/dav/",
        username="user",
        password="pass",
        root="notes",
        interval_seconds=30,
    )

    async def loader() -> KnowledgeSettings:
        return config

    runtime = KnowledgeSyncRuntime(
        config_loader=loader,
        client_factory=lambda settings: dav,
        local_dir=local,
        state_path=state,
    )
    await runtime.start()
    for _ in range(50):
        if runtime.last_sync_at:
            break
        await asyncio.sleep(0.02)
    await runtime.stop()

    assert not (local / "old.md").exists()
    assert (local / "new.md").read_bytes() == b"new"
    assert list(tmp_path.glob("knowledge.old-*"))
