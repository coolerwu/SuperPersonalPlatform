import asyncio
import json

import httpx
import pytest

from server.app.webdav_context_service import WebDAVContextError, WebDAVContextService
from server.infrastructure.config import parse_settings
from server.infrastructure.nutstore_webdav import NutstoreWebDAVClient


def test_context_webdav_config_parses_single_sync_root_and_permissions() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "nutstore": {"enabled": True, "username": "u", "password": "p"},
            "context": {
                "webdav_sync": {
                    "enabled": True,
                    "root_path": "/notebook/",
                    "interval_seconds": 600,
                    "extensions": ["md", ".txt"],
                },
                "webdav_permissions": [
                    {
                        "path": "/",
                        "readable": True,
                        "writable": False,
                        "protected": True,
                    },
                    {
                        "path": "/00AgentInbox",
                        "readable": True,
                        "writable": True,
                        "protected": False,
                    },
                ],
            },
        }
    )

    assert settings.context.webdav_sync.enabled is True
    assert settings.context.webdav_sync.root_path == "/notebook"
    assert settings.context.webdav_sync.extensions == (".md", ".txt")
    assert [permission.path for permission in settings.context.webdav_permissions] == ["/", "/00AgentInbox"]
    assert settings.context.webdav_permissions[0].protected is True
    assert settings.context.webdav_permissions[1].writable is True


def test_webdav_context_refresh_uses_single_root_and_permission_paths(tmp_path) -> None:
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.method == "PROPFIND" and str(request.url).endswith("/dav/notebook/"):
            return httpx.Response(
                207,
                text="""
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/notebook/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/notebook/rules.md</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getcontentlength>5</d:getcontentlength>
      <d:getlastmodified>Sat, 22 Aug 2026 01:00:00 GMT</d:getlastmodified>
      <d:getetag>"rules"</d:getetag>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/notebook/00AgentInbox/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
</d:multistatus>
""",
            )
        if request.method == "PROPFIND" and str(request.url).endswith("/dav/notebook/00AgentInbox/"):
            return httpx.Response(
                207,
                text="""
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/notebook/00AgentInbox/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/notebook/00AgentInbox/write.md</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getcontentlength>5</d:getcontentlength>
      <d:getlastmodified>Sat, 22 Aug 2026 01:00:00 GMT</d:getlastmodified>
      <d:getetag>"write"</d:getetag>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>
""",
            )
        if request.method == "GET":
            if str(request.url).endswith("/rules.md"):
                return httpx.Response(200, content="rules".encode())
            return httpx.Response(200, content="write".encode())
        return httpx.Response(500)

    service = _service(tmp_path, httpx.MockTransport(handler))

    asyncio.run(service.refresh())

    documents = {item.path: item.content for item in service.documents()}
    assert documents == {
        "/webdav/00AgentInbox/write.md": "write",
        "/webdav/rules.md": "rules",
    }
    index = json.loads((tmp_path / "context" / "webdav" / "index.json").read_text(encoding="utf-8"))
    assert index["files"]["/webdav/rules.md"]["permission_path"] == "/"
    assert index["files"]["/webdav/00AgentInbox/write.md"]["permission_path"] == "/00AgentInbox"
    assert index["files"]["/webdav/rules.md"]["cache_path"] == "files/rules.md"
    assert index["files"]["/webdav/00AgentInbox/write.md"]["cache_path"] == "files/00AgentInbox/write.md"
    assert (tmp_path / "context" / "webdav" / "files" / "rules.md").read_text(encoding="utf-8") == "rules"
    assert (
        tmp_path / "context" / "webdav" / "files" / "00AgentInbox" / "write.md"
    ).read_text(encoding="utf-8") == "write"
    assert requested_urls.count("https://dav.jianguoyun.com/dav/notebook/") == 1
    assert requested_urls.count("https://dav.jianguoyun.com/dav/notebook/00AgentInbox/") == 1


def test_webdav_context_refresh_caches_markdown_referenced_assets(tmp_path) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nasset"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND" and str(request.url).endswith("/dav/notebook/"):
            return httpx.Response(
                207,
                text="""
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/notebook/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/notebook/report.md</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getcontentlength>28</d:getcontentlength>
      <d:getlastmodified>Sat, 22 Aug 2026 01:00:00 GMT</d:getlastmodified>
      <d:getetag>"report"</d:getetag>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/notebook/images/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
</d:multistatus>
""",
            )
        if request.method == "PROPFIND" and str(request.url).endswith("/dav/notebook/images/"):
            return httpx.Response(
                207,
                text="""
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/notebook/images/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/notebook/images/chart.png</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getcontentlength>13</d:getcontentlength>
      <d:getlastmodified>Sat, 22 Aug 2026 01:00:01 GMT</d:getlastmodified>
      <d:getetag>"chart"</d:getetag>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>
""",
            )
        if request.method == "GET" and str(request.url).endswith("/report.md"):
            return httpx.Response(200, content="报告\n\n![chart](images/chart.png)".encode())
        if request.method == "GET" and str(request.url).endswith("/images/chart.png"):
            return httpx.Response(200, content=png_bytes)
        return httpx.Response(500)

    service = _service(tmp_path, httpx.MockTransport(handler))

    asyncio.run(service.refresh())

    assert [(item.path, item.content) for item in service.documents()] == [
        ("/webdav/report.md", "报告\n\n![chart](images/chart.png)")
    ]
    index = json.loads((tmp_path / "context" / "webdav" / "index.json").read_text(encoding="utf-8"))
    assert index["files"]["/webdav/report.md"]["kind"] == "document"
    assert index["files"]["/webdav/images/chart.png"]["kind"] == "asset"
    assert index["files"]["/webdav/report.md"]["cache_path"] == "files/report.md"
    assert index["files"]["/webdav/images/chart.png"]["cache_path"] == "files/images/chart.png"
    asset_cache = tmp_path / "context" / "webdav" / index["files"]["/webdav/images/chart.png"]["cache_path"]
    assert asset_cache.read_bytes() == png_bytes
    assert service.summary()["documents"] == 1
    assert service.summary()["assets"] == 1


def test_webdav_context_write_rejects_protected_parent_path(tmp_path) -> None:
    service = _service(tmp_path, httpx.MockTransport(lambda request: httpx.Response(500)))

    with pytest.raises(WebDAVContextError, match="protected"):
        asyncio.run(
            service.write(
                absolute_path="/webdav/rules.md",
                content="不能写",
                mode="overwrite",
            )
        )


def test_webdav_context_write_uses_writable_child_permission(tmp_path) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url), request.content))
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(500)

    service = _service(tmp_path, httpx.MockTransport(handler))

    result = asyncio.run(
        service.write(
            absolute_path="/webdav/00AgentInbox/new.md",
            content="候选知识",
            mode="create",
        )
    )

    assert result["path"] == "/webdav/00AgentInbox/new.md"
    assert (tmp_path / "context" / "webdav" / "files" / "00AgentInbox" / "new.md").read_text(
        encoding="utf-8"
    ) == "候选知识"
    assert [item[0] for item in requests] == ["GET", "MKCOL", "MKCOL", "PUT"]
    assert str(requests[-1][1]).endswith("/dav/notebook/00AgentInbox/new.md")


def test_webdav_context_remote_paths_still_respect_nutstore_root_path(tmp_path) -> None:
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(500)

    service = _service(tmp_path, httpx.MockTransport(handler), nutstore_root_path="/Apps/DeepAgent")

    asyncio.run(
        service.write(
            absolute_path="/webdav/00AgentInbox/scoped.md",
            content="workspace scoped",
            mode="create",
        )
    )

    assert requested_urls[-1].endswith("/dav/Apps/DeepAgent/notebook/00AgentInbox/scoped.md")


def test_webdav_context_refresh_prunes_flat_legacy_cache_files(tmp_path) -> None:
    legacy_cache = tmp_path / "context" / "webdav" / "files" / "webdav__rules.md"
    legacy_cache.parent.mkdir(parents=True, exist_ok=True)
    legacy_cache.write_text("old", encoding="utf-8")
    requested_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested_reads
        if request.method == "PROPFIND" and str(request.url).endswith("/dav/notebook/"):
            return httpx.Response(
                207,
                text="""
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/notebook/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/notebook/rules.md</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getcontentlength>5</d:getcontentlength>
      <d:getlastmodified>Sat, 22 Aug 2026 01:00:00 GMT</d:getlastmodified>
      <d:getetag>"rules"</d:getetag>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>
""",
            )
        if request.method == "GET" and str(request.url).endswith("/rules.md"):
            requested_reads += 1
            return httpx.Response(200, content=b"rules")
        return httpx.Response(500)

    service = _service(tmp_path, httpx.MockTransport(handler))

    asyncio.run(service.refresh())

    assert requested_reads == 1
    assert not legacy_cache.exists()
    assert (tmp_path / "context" / "webdav" / "files" / "rules.md").read_text(encoding="utf-8") == "rules"


def test_webdav_context_recent_documents_sort_by_modified_time(tmp_path) -> None:
    cache_dir = tmp_path / "context" / "webdav"
    (cache_dir / "files" / "old.md").parent.mkdir(parents=True, exist_ok=True)
    (cache_dir / "files" / "old.md").write_text("旧笔记内容", encoding="utf-8")
    (cache_dir / "files" / "new.md").write_text("# 新笔记\n\n今天的内容", encoding="utf-8")
    (cache_dir / "index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-08-23T00:00:00+00:00",
                "files": {
                    "/webdav/old.md": {
                        "kind": "document",
                        "cache_path": "files/old.md",
                        "modified": "Sat, 22 Aug 2026 01:00:00 GMT",
                        "size": 12,
                    },
                    "/webdav/new.md": {
                        "kind": "document",
                        "cache_path": "files/new.md",
                        "modified": "2026-08-23T01:00:00+00:00",
                        "size": 20,
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = _service(tmp_path, httpx.MockTransport(lambda request: httpx.Response(500)))

    recent = service.recent_documents(limit=2)

    assert [item.path for item in recent] == ["/webdav/new.md", "/webdav/old.md"]
    assert recent[0].snippet == "# 新笔记"


def _service(tmp_path, transport: httpx.MockTransport, *, nutstore_root_path: str = "/") -> WebDAVContextService:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "nutstore": {
                "enabled": True,
                "username": "u",
                "password": "p",
                "root_path": nutstore_root_path,
            },
            "context": {
                "webdav_sync": {
                    "enabled": True,
                    "root_path": "/notebook",
                    "interval_seconds": 600,
                    "max_files_per_root": 50,
                    "max_file_size_bytes": 10000,
                    "extensions": [".md", ".txt", ".json", ".jsonl"],
                },
                "webdav_permissions": [
                    {
                        "path": "/",
                        "readable": True,
                        "writable": False,
                        "protected": True,
                    },
                    {
                        "path": "/00AgentInbox",
                        "readable": True,
                        "writable": True,
                        "protected": False,
                    },
                ],
            },
        }
    )
    client = NutstoreWebDAVClient(settings.nutstore, transport=transport)
    return WebDAVContextService(
        workspace=tmp_path,
        nutstore=settings.nutstore,
        context=settings.context,
        client=client,
    )
