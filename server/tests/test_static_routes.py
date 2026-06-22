from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.dependencies import AppContainer
from server.adapter.static_routes import mount_frontend
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.proxy_service import ProxyService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import SystemUpdateService
from server.domain.auth import AuthToken
from server.domain.proxy import ProxyRequest, ProxyResponse
from server.infrastructure.session import SessionCodec
from server.infrastructure.fastapi_app import create_app
from server.infrastructure.config import load_settings


class StaticProxyGateway:
    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        return ProxyResponse(status_code=200, headers={}, body=b"")


def make_static_client(tmp_path) -> TestClient:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    app = FastAPI()
    token = "secret-token"
    container = AppContainer(
        auth_service=AuthService(AuthToken(token)),
        config_file_service=ConfigFileService(tmp_path),
        proxy_service=ProxyService(StaticProxyGateway()),
        system_log_service=SystemLogService(tmp_path),
        system_update_service=SystemUpdateService(tmp_path, tmp_path),
        session_codec=SessionCodec(token),
    )
    from server.adapter.auth_routes import create_auth_router

    app.include_router(create_auth_router(container))
    mount_frontend(app, container, dist_dir)
    return TestClient(app)


def test_frontend_route_redirects_when_unauthenticated(tmp_path) -> None:
    client = make_static_client(tmp_path)

    response = client.get("/proxy", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_redirects_when_unauthenticated(tmp_path) -> None:
    client = make_static_client(tmp_path)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_frontend_route_serves_index_with_dev_auth_bypass(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUPER_PERSONAL_RELOAD", "1")
    monkeypatch.setenv("SUPER_PERSONAL_DEV_AUTH_BYPASS", "1")
    client = make_static_client(tmp_path)

    response = client.get("/agents", follow_redirects=False)

    assert response.status_code == 200
    assert "root" in response.text


def test_frontend_route_serves_index_when_authenticated(tmp_path) -> None:
    client = make_static_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.get("/proxy")

    assert response.status_code == 200
    assert "root" in response.text


def test_removed_self_dev_and_terminal_routes_are_not_registered(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(
        "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n",
        encoding="utf-8",
    )

    app = create_app(load_settings(tmp_path / "config.yaml"), tmp_path)
    paths = {route.path for route in app.routes}

    assert not any(path.startswith("/api/self-dev") for path in paths)
    assert "/api/system/terminal/connect" not in paths
