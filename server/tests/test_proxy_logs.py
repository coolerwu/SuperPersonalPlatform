from fastapi.testclient import TestClient

from server.adapter.dependencies import AppContainer
from server.app.auth_service import AuthService
from server.app.logs_service import LogsService
from server.domain.auth import AuthToken
from server.domain.errors import UpstreamLogsError
from server.domain.logs import LogsPayload
from server.infrastructure.config import AuthConfig, ProxyConfig, ServerConfig, Settings
from server.infrastructure.fastapi_app import create_app, create_container
from server.infrastructure.session import SessionCodec


class StaticLogsGateway:
    def __init__(self, payload: LogsPayload) -> None:
        self._payload = payload

    async def fetch_logs(self) -> LogsPayload:
        return self._payload


class FailingLogsGateway:
    async def fetch_logs(self) -> LogsPayload:
        raise UpstreamLogsError("boom")


def authenticated_client_with_gateway(gateway) -> TestClient:
    settings = Settings(
        auth=AuthConfig(token="secret-token"),
        proxy=ProxyConfig(logs_url="http://example.test/logs"),
        server=ServerConfig(),
    )
    app = create_app(settings)
    container = AppContainer(
        auth_service=AuthService(AuthToken(settings.auth.token)),
        logs_service=LogsService(gateway),
        session_codec=SessionCodec(settings.auth.token),
    )
    app.router.routes.clear()
    from server.adapter.auth_routes import create_auth_router
    from server.adapter.proxy_routes import create_proxy_router

    app.include_router(create_auth_router(container))
    app.include_router(create_proxy_router(container))

    client = TestClient(app)
    client.post("/api/auth/login", json={"token": "secret-token"})
    return client


def test_logs_requires_authentication() -> None:
    settings = Settings(
        auth=AuthConfig(token="secret-token"),
        proxy=ProxyConfig(logs_url="http://example.test/logs"),
        server=ServerConfig(),
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/proxy/logs")

    assert response.status_code == 401


def test_logs_returns_json_payload() -> None:
    client = authenticated_client_with_gateway(
        StaticLogsGateway(LogsPayload.from_json([{"level": "info", "message": "ok"}]))
    )

    response = client.get("/api/proxy/logs")

    assert response.status_code == 200
    assert response.json() == {
        "type": "json",
        "data": [{"level": "info", "message": "ok"}],
    }


def test_logs_returns_text_lines() -> None:
    client = authenticated_client_with_gateway(
        StaticLogsGateway(LogsPayload.from_text("one\n\ntwo\n"))
    )

    response = client.get("/api/proxy/logs")

    assert response.status_code == 200
    assert response.json() == {"type": "text", "data": ["one", "two"]}


def test_logs_returns_bad_gateway_for_upstream_errors() -> None:
    client = authenticated_client_with_gateway(FailingLogsGateway())

    response = client.get("/api/proxy/logs")

    assert response.status_code == 502
