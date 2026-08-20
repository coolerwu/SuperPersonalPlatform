import asyncio

import httpx
import pytest

from server.domain.agents import AgentConfigError
from server.infrastructure.config import NutstoreConfig, parse_settings
from server.infrastructure.nutstore_webdav import NutstoreWebDAVClient


def test_nutstore_config_defaults_and_parses_credentials() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "nutstore": {
                "enabled": True,
                "username": "user@example.com",
                "password": "app-password",
                "root_path": "/Apps/Agent",
            },
        }
    )

    assert settings.nutstore.enabled is True
    assert settings.nutstore.base_url == "https://dav.jianguoyun.com/dav/"
    assert settings.nutstore.username == "user@example.com"
    assert settings.nutstore.password == "app-password"
    assert settings.nutstore.root_path == "/Apps/Agent"


def test_nutstore_webdav_lists_reads_writes_and_deletes() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PROPFIND":
            return httpx.Response(
                207,
                text="""
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/Apps/Agent/docs/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/Apps/Agent/docs/a.txt</d:href>
    <d:propstat><d:prop><d:resourcetype/><d:getcontentlength>5</d:getcontentlength></d:prop></d:propstat>
  </d:response>
</d:multistatus>
""",
            )
        if request.method == "GET":
            return httpx.Response(200, content=b"hello world")
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            return httpx.Response(201)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(500)

    client = NutstoreWebDAVClient(
        NutstoreConfig(enabled=True, username="user@example.com", password="app-password", root_path="/Apps/Agent"),
        transport=httpx.MockTransport(handler),
    )

    entries = asyncio.run(client.list("docs"))
    content, truncated = asyncio.run(client.read_bytes("docs/a.txt", max_bytes=5))
    asyncio.run(client.write_bytes("docs/new/b.txt", b"body"))
    asyncio.run(client.delete("docs/a.txt"))

    assert [(entry.path, entry.name, entry.is_dir, entry.size) for entry in entries] == [
        ("/Apps/Agent/docs/a.txt", "a.txt", False, 5)
    ]
    assert content == b"hello"
    assert truncated is True
    assert [request.method for request in requests] == ["PROPFIND", "GET", "MKCOL", "MKCOL", "PUT", "DELETE"]


def test_nutstore_webdav_rejects_unsafe_paths() -> None:
    client = NutstoreWebDAVClient(
        NutstoreConfig(enabled=True, username="user@example.com", password="app-password"),
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )

    with pytest.raises(AgentConfigError, match="must not contain"):
        asyncio.run(client.read_bytes("../secret.txt"))
