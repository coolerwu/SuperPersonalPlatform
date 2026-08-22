import asyncio
import json

import httpx
import pytest

from server.app.webdav_context_service import WebDAVContextError, WebDAVContextService
from server.infrastructure.config import NutstoreConfig, parse_settings
from server.infrastructure.nutstore_webdav import NutstoreWebDAVClient


def test_context_webdav_config_parses_roots_and_sync() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "nutstore": {"enabled": True, "username": "u", "password": "p"},
            "context": {
                "webdav_sync": {"enabled": True, "interval_seconds": 600, "extensions": ["md", ".txt"]},
                "webdav_roots": [
                    {
                        "id": "my_notes",
                        "name": "我的心得",
                        "path": "/Knowledge/notes",
                        "readable": True,
                        "writable": False,
                        "protected": True,
                    }
                ],
            },
        }
    )

    assert settings.context.webdav_sync.enabled is True
    assert settings.context.webdav_sync.extensions == (".md", ".txt")
    assert settings.context.webdav_roots[0].protected is True


def test_webdav_context_refresh_caches_readable_roots(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            if "AgentWorkspace/inbox" in str(request.url):
                return httpx.Response(
                    207,
                    text="<d:multistatus xmlns:d=\"DAV:\"><d:response><d:href>/dav/AgentWorkspace/inbox/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>",
                )
            return httpx.Response(
                207,
                text="""
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/Knowledge/notes/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/Knowledge/notes/a.md</d:href>
    <d:propstat><d:prop>
      <d:resourcetype/>
      <d:getcontentlength>12</d:getcontentlength>
      <d:getlastmodified>Sat, 22 Aug 2026 01:00:00 GMT</d:getlastmodified>
      <d:getetag>"abc"</d:getetag>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>
""",
            )
        if request.method == "GET":
            return httpx.Response(200, content="长期规则：结论先行".encode())
        return httpx.Response(500)

    service = _service(tmp_path, httpx.MockTransport(handler))

    asyncio.run(service.refresh())

    documents = service.documents()
    assert [(item.path, item.content) for item in documents] == [
        ("/webdav/my_notes/a.md", "长期规则：结论先行")
    ]
    index = json.loads((tmp_path / "context" / "state" / "webdav_cache" / "index.json").read_text(encoding="utf-8"))
    assert index["files"]["/webdav/my_notes/a.md"]["etag"] == "abc"


def test_webdav_context_write_rejects_protected_root(tmp_path) -> None:
    service = _service(tmp_path, httpx.MockTransport(lambda request: httpx.Response(500)))

    with pytest.raises(WebDAVContextError, match="protected"):
        asyncio.run(
            service.write(
                absolute_path="/webdav/my_notes/a.md",
                content="不能写",
                mode="overwrite",
            )
        )


def test_webdav_context_write_updates_writable_root_cache(tmp_path) -> None:
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
            absolute_path="/webdav/agent_inbox/new.md",
            content="候选知识",
            mode="create",
        )
    )

    assert result["path"] == "/webdav/agent_inbox/new.md"
    assert (tmp_path / "context" / "state" / "webdav_cache" / "files" / "webdav__agent_inbox__new.md").read_text(
        encoding="utf-8"
    ) == "候选知识"
    assert [item[0] for item in requests] == ["GET", "MKCOL", "MKCOL", "PUT"]


def _service(tmp_path, transport: httpx.MockTransport) -> WebDAVContextService:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "nutstore": {
                "enabled": True,
                "username": "u",
                "password": "p",
            },
            "context": {
                "webdav_sync": {
                    "enabled": True,
                    "interval_seconds": 600,
                    "max_files_per_root": 50,
                    "max_file_size_bytes": 10000,
                    "extensions": [".md", ".txt", ".json", ".jsonl"],
                },
                "webdav_roots": [
                    {
                        "id": "my_notes",
                        "name": "我的心得",
                        "path": "/Knowledge/notes",
                        "readable": True,
                        "writable": False,
                        "protected": True,
                    },
                    {
                        "id": "agent_inbox",
                        "name": "Agent 写入区",
                        "path": "/AgentWorkspace/inbox",
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
