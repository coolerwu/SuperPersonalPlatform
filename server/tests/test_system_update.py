import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.adapter.system_routes import create_system_router
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.proxy_service import ProxyService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import (
    SystemUpdateService,
    UpdateAlreadyRunningError,
)
from server.domain.auth import AuthToken
from server.domain.proxy import ProxyRequest, ProxyResponse
from server.infrastructure.session import SessionCodec
from server.infrastructure.config import AuthConfig, ProxyConfig, ServerConfig, Settings
from server.infrastructure.fastapi_app import create_app


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
        return Path("logs/platform-2026-05-06.log")


def make_client(update_service, workspace: Path | None = None) -> TestClient:
    token = "secret-token"
    workspace = workspace or Path.cwd()
    system_log_service = SystemLogService(workspace)
    container = AppContainer(
        auth_service=AuthService(AuthToken(token)),
        config_file_service=ConfigFileService(workspace),
        proxy_service=ProxyService(EmptyProxyGateway()),
        system_log_service=system_log_service,
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


def test_system_logs_require_authentication(tmp_path) -> None:
    client = make_client(FakeSystemUpdateService(), tmp_path)

    response = client.post("/api/system/logs/list")

    assert response.status_code == 401


def test_system_config_requires_authentication(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(
        "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n",
        encoding="utf-8",
    )
    client = make_client(FakeSystemUpdateService(), tmp_path)

    response = client.post("/api/system/config/read")

    assert response.status_code == 401


def test_system_config_reads_workspace_config(tmp_path) -> None:
    content = "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n"
    (tmp_path / "config.yaml").write_text(content, encoding="utf-8")
    client = make_client(FakeSystemUpdateService(), tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/system/config/read")

    assert response.status_code == 200
    assert response.json()["path"] == str(tmp_path / "config.yaml")
    assert response.json()["content"] == content


def test_system_config_get_does_not_return_config(tmp_path) -> None:
    content = "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n"
    (tmp_path / "config.yaml").write_text(content, encoding="utf-8")
    client = make_client(FakeSystemUpdateService(), tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.get("/api/system/config")

    assert response.status_code == 405
    assert content not in response.text


def test_system_config_route_precedes_api_proxy_fallback(tmp_path, monkeypatch) -> None:
    content = "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n"
    (tmp_path / "config.yaml").write_text(content, encoding="utf-8")
    monkeypatch.setenv("SUPER_PERSONAL_WORKSPACE", str(tmp_path))
    settings = Settings(
        auth=AuthConfig(token="secret-token"),
        proxy=ProxyConfig(upstream_base_url="http://example.test/"),
        server=ServerConfig(),
    )
    client = TestClient(create_app(settings, workspace=tmp_path))
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/system/config/read")

    assert response.status_code == 200
    assert response.json()["content"] == content


def test_system_config_saves_valid_yaml(tmp_path) -> None:
    original = "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n"
    updated = "auth:\n  token: next-token\nproxy:\n  upstream_base_url: http://localhost:9119/\n"
    (tmp_path / "config.yaml").write_text(original, encoding="utf-8")
    client = make_client(FakeSystemUpdateService(), tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.put("/api/system/config", json={"content": updated})

    assert response.status_code == 200
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == updated


def test_system_config_rejects_invalid_yaml_without_overwriting(tmp_path) -> None:
    original = "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n"
    (tmp_path / "config.yaml").write_text(original, encoding="utf-8")
    client = make_client(FakeSystemUpdateService(), tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.put("/api/system/config", json={"content": "auth: []\n"})

    assert response.status_code == 400
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == original


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


def test_system_logs_list_returns_platform_logs_and_cleans_old_files(tmp_path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    current = logs_dir / "platform-2026-05-06.log"
    older = logs_dir / "platform-2026-05-05.log"
    expired = logs_dir / "platform-2026-05-01.log"
    ignored = logs_dir / "other.log"
    current.write_text("current", encoding="utf-8")
    older.write_text("older", encoding="utf-8")
    expired.write_text("expired", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")
    now = time.time()
    os.utime(current, (now, now))
    os.utime(older, (now - 60, now - 60))
    os.utime(expired, (now - 4 * 24 * 60 * 60, now - 4 * 24 * 60 * 60))
    client = make_client(FakeSystemUpdateService(), tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/system/logs/list")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["logs"]] == [
        "platform-2026-05-06.log",
        "platform-2026-05-05.log",
    ]
    assert not expired.exists()
    assert ignored.exists()


def test_system_logs_read_returns_tail_content(tmp_path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_path = logs_dir / "platform-2026-05-06.log"
    log_path.write_text("a" * (200 * 1024) + "tail", encoding="utf-8")
    client = make_client(FakeSystemUpdateService(), tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/system/logs/read", json={"name": log_path.name})

    assert response.status_code == 200
    assert response.json()["name"] == log_path.name
    assert response.json()["content"].endswith("tail")
    assert len(response.json()["content"].encode("utf-8")) == 200 * 1024
    assert response.json()["truncated"] is True


def test_system_logs_read_rejects_unsafe_names(tmp_path) -> None:
    client = make_client(FakeSystemUpdateService(), tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/system/logs/read", json={"name": "../config.yaml"})

    assert response.status_code == 400


def test_system_logs_read_returns_not_found(tmp_path) -> None:
    client = make_client(FakeSystemUpdateService(), tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post(
        "/api/system/logs/read",
        json={"name": "platform-2026-05-06.log"},
    )

    assert response.status_code == 404


def test_system_update_service_lock_blocks_duplicate_starts(tmp_path, monkeypatch) -> None:
    calls = []

    class FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = os.getpid()
            calls.append((args, kwargs))

    monkeypatch.setattr("server.app.system_update_service.subprocess.Popen", FakePopen)
    service = SystemUpdateService(tmp_path, tmp_path)

    log_path = service.start_update()

    assert log_path.parent == tmp_path / "logs"
    assert log_path.name.startswith("platform-")
    assert log_path.name.endswith(".log")
    assert calls
    assert calls[0][0][0][:3] == ["/bin/sh", "-c", calls[0][0][0][2]]
    assert f"--workspace {tmp_path}" in calls[0][0][0][2]
    assert str(log_path) in calls[0][0][0][2]
    assert calls[0][1]["start_new_session"] is True
    try:
        service.start_update()
    except UpdateAlreadyRunningError:
        pass
    else:
        raise AssertionError("expected UpdateAlreadyRunningError")


def test_system_update_service_removes_stale_legacy_lock(tmp_path, monkeypatch) -> None:
    calls = []
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    (run_dir / "update-service.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")

    class FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = os.getpid()
            calls.append((args, kwargs))

    monkeypatch.setattr("server.app.system_update_service.subprocess.Popen", FakePopen)
    service = SystemUpdateService(tmp_path, tmp_path)

    service.start_update()

    assert calls
    assert '"pid":' in (run_dir / "update-service.lock").read_text(encoding="utf-8")


def test_system_update_service_removes_dead_json_lock(tmp_path, monkeypatch) -> None:
    calls = []
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    (run_dir / "update-service.lock").write_text(
        '{"pid": 99999999, "started_at": "2026-05-06T10:00:00"}\n',
        encoding="utf-8",
    )

    class FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = os.getpid()
            calls.append((args, kwargs))

    monkeypatch.setattr("server.app.system_update_service.subprocess.Popen", FakePopen)
    service = SystemUpdateService(tmp_path, tmp_path)

    service.start_update()

    assert calls
