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


def test_context_webdav_config_migrates_legacy_roots() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "nutstore": {"enabled": True, "username": "u", "password": "p"},
            "context": {
                "webdav_sync": {"enabled": True, "interval_seconds": 600},
                "webdav_roots": [
                    {
                        "id": "notebook",
                        "name": "notebook",
                        "path": "/notebook",
                        "readable": True,
                        "writable": False,
                        "protected": True,
                    },
                    {
                        "id": "agent_inbox",
                        "name": "agent_inbox",
                        "path": "/notebook/00AgentInbox",
                        "readable": True,
                        "writable": True,
                        "protected": False,
                    },
                ],
            },
        }
    )

    assert settings.context.webdav_sync.root_path == "/notebook"
    assert [
        (permission.path, permission.readable, permission.writable, permission.protected)
        for permission in settings.context.webdav_permissions
    ] == [
        ("/", True, False, True),
        ("/00AgentInbox", True, True, False),
    ]


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
    assert requested_urls.count("https://dav.jianguoyun.com/dav/notebook/") == 1
    assert requested_urls.count("https://dav.jianguoyun.com/dav/notebook/00AgentInbox/") == 1


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
    assert (tmp_path / "context" / "webdav" / "files" / "webdav__00AgentInbox__new.md").read_text(
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
