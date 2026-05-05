from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.adapter.system_routes import create_system_router
from server.app.auth_service import AuthService
from server.app.proxy_service import ProxyService
from server.app.system_update_service import (
    SystemUpdateService,
    UpdateAlreadyRunningError,
)
from server.domain.auth import AuthToken
from server.domain.proxy import ProxyRequest, ProxyResponse
from server.infrastructure.session import SessionCodec


class EmptyProxyGateway:
    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        return ProxyResponse(status_code=200, headers={}, body=b"")


class FakeSystemUpdateService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.called = False

    def start_update(self) -> Path:
        self.called = True
        if self.error:
            raise self.error
        return Path(".run/update-service.log")


def make_client(update_service) -> TestClient:
    token = "secret-token"
    container = AppContainer(
        auth_service=AuthService(AuthToken(token)),
        proxy_service=ProxyService(EmptyProxyGateway()),
        system_update_service=update_service,
        session_codec=SessionCodec(token),
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_system_router(container))
    return TestClient(app)


def test_update_service_requires_authentication() -> None:
    update_service = FakeSystemUpdateService()
    client = make_client(update_service)

    response = client.post("/api/system/update-service")

    assert response.status_code == 401
    assert update_service.called is False


def test_update_service_starts_when_authenticated() -> None:
    update_service = FakeSystemUpdateService()
    client = make_client(update_service)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/system/update-service")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["message"] == "更新已开始"
    assert update_service.called is True


def test_update_service_returns_conflict_when_running() -> None:
    update_service = FakeSystemUpdateService(UpdateAlreadyRunningError())
    client = make_client(update_service)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/system/update-service")

    assert response.status_code == 409
    assert response.json()["detail"] == "更新任务已经在执行"


def test_system_update_service_lock_blocks_duplicate_starts(tmp_path, monkeypatch) -> None:
    calls = []

    class FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            calls.append((args, kwargs))

    monkeypatch.setattr("server.app.system_update_service.subprocess.Popen", FakePopen)
    service = SystemUpdateService(tmp_path)

    log_path = service.start_update()

    assert log_path == tmp_path / ".run" / "update-service.log"
    assert calls
    try:
        service.start_update()
    except UpdateAlreadyRunningError:
        pass
    else:
        raise AssertionError("expected UpdateAlreadyRunningError")
