import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.adapter.proxy_routes import create_api_fallback_proxy_router, create_proxy_router
from server.adapter.proxy_routes import create_root_asset_proxy_router
from server.app.auth_service import AuthService
from server.app.proxy_service import ProxyService
from server.app.system_update_service import SystemUpdateService
from server.domain.auth import AuthToken
from server.domain.errors import UpstreamProxyError
from server.domain.proxy import ProxyRequest, ProxyResponse
from server.infrastructure.config import AuthConfig, ProxyConfig, ServerConfig, Settings
from server.infrastructure.fastapi_app import create_app
from server.infrastructure.http_proxy_gateway import HttpProxyGateway
from server.infrastructure.session import SessionCodec


class RecordingProxyGateway:
    def __init__(self, response: ProxyResponse | None = None) -> None:
        self.requests: list[ProxyRequest] = []
        self._response = response or ProxyResponse(
            status_code=200,
            headers={"content-type": "text/html", "connection": "closed"},
            body=b"<html>ok</html>",
        )

    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        self.requests.append(request)
        return self._response


class FailingProxyGateway:
    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        raise UpstreamProxyError("boom")


def authenticated_client_with_gateway(gateway) -> TestClient:
    token = "secret-token"
    container = AppContainer(
        auth_service=AuthService(AuthToken(token)),
        proxy_service=ProxyService(gateway),
        system_update_service=SystemUpdateService(Path.cwd()),
        session_codec=SessionCodec(token),
    )
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_proxy_router(container))
    app.include_router(create_api_fallback_proxy_router(container))
    app.include_router(create_root_asset_proxy_router(container))
    client = TestClient(app)
    client.post("/api/auth/login", json={"token": token})
    return client


def test_proxy_site_requires_authentication() -> None:
    settings = Settings(
        auth=AuthConfig(token="secret-token"),
        proxy=ProxyConfig(upstream_base_url="http://example.test/"),
        server=ServerConfig(),
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/proxy/site/")

    assert response.status_code == 401


def test_proxy_site_forwards_get_path_and_query() -> None:
    gateway = RecordingProxyGateway()
    client = authenticated_client_with_gateway(gateway)

    response = client.get("/api/proxy/site/a/b?x=1")

    assert response.status_code == 200
    assert gateway.requests[0].method == "GET"
    assert gateway.requests[0].path == "a/b"
    assert gateway.requests[0].query_string == b"x=1"


def test_proxy_site_forwards_post_body_and_content_type() -> None:
    gateway = RecordingProxyGateway(
        ProxyResponse(
            status_code=201,
            headers={"content-type": "application/json"},
            body=b'{"ok":true}',
        )
    )
    client = authenticated_client_with_gateway(gateway)

    response = client.post(
        "/api/proxy/site/api/items",
        content=b'{"name":"demo"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 201
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"ok":true}'
    assert gateway.requests[0].body == b'{"name":"demo"}'
    assert gateway.requests[0].headers["content-type"] == "application/json"


def test_unknown_api_request_falls_back_to_upstream_api_path() -> None:
    gateway = RecordingProxyGateway()
    client = authenticated_client_with_gateway(gateway)

    response = client.get("/api/status?detail=1")

    assert response.status_code == 200
    assert gateway.requests[0].path == "api/status"
    assert gateway.requests[0].query_string == b"detail=1"


def test_known_root_asset_request_falls_back_to_upstream_path() -> None:
    gateway = RecordingProxyGateway()
    client = authenticated_client_with_gateway(gateway)

    response = client.get("/dashboard-plugins/example/dist/index.js")

    assert response.status_code == 200
    assert gateway.requests[0].path == "dashboard-plugins/example/dist/index.js"


def test_proxy_site_returns_bad_gateway_for_upstream_errors() -> None:
    client = authenticated_client_with_gateway(FailingProxyGateway())

    response = client.get("/api/proxy/site/")

    assert response.status_code == 502


def test_http_proxy_gateway_builds_target_url_and_filters_headers() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html; charset=utf-8",
                "connection": "close",
                "cache-control": "no-store",
            },
            content=b'<a href="/home"><img src="/logo.png"><form action="/save"></form></a>',
            request=request,
        )

    transport = httpx.MockTransport(handler)
    gateway = HttpProxyGateway("http://192.168.1.3:9119/", transport=transport)

    response = asyncio.run(
        gateway.forward(
            ProxyRequest(
                method="GET",
                path="a/b",
                query_string=b"x=1",
                headers={"host": "localhost:8888", "connection": "keep-alive"},
                body=b"",
            )
        )
    )

    assert captured["url"] == "http://192.168.1.3:9119/a/b?x=1"
    assert captured["headers"]["host"] == "192.168.1.3:9119"
    assert captured["headers"]["connection"] != "keep-alive, custom"
    assert response.headers == {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "content-length": str(len(response.body)),
    }
    assert b'href="/api/proxy/site/home"' in response.body
    assert b'src="/api/proxy/site/logo.png"' in response.body
    assert b'action="/api/proxy/site/save"' in response.body


def test_http_proxy_gateway_rewrites_javascript_root_relative_api_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/javascript"},
            content=b'fetch("/api/status"); import("/assets/chunk.js"); "/fonts/app.woff2";',
            request=request,
        )

    gateway = HttpProxyGateway(
        "http://192.168.1.3:9119/",
        transport=httpx.MockTransport(handler),
    )

    response = asyncio.run(
        gateway.forward(
            ProxyRequest(
                method="GET",
                path="assets/index.js",
                query_string=b"",
                headers={},
                body=b"",
            )
        )
    )

    assert b'fetch("/api/proxy/site/api/status")' in response.body
    assert b'import("/api/proxy/site/assets/chunk.js")' in response.body
    assert b'"/api/proxy/site/fonts/app.woff2"' in response.body
