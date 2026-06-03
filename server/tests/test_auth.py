from fastapi.testclient import TestClient
from pathlib import Path

from server.infrastructure.config import AuthConfig, ProxyConfig, ServerConfig, Settings
from server.infrastructure.fastapi_app import create_app


def make_client(workspace: Path) -> TestClient:
    settings = Settings(
        auth=AuthConfig(token="secret-token"),
        proxy=ProxyConfig(upstream_base_url="http://example.test/"),
        server=ServerConfig(),
    )
    return TestClient(create_app(settings, workspace=workspace))


def test_login_success_sets_authenticated_session(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post("/api/auth/login", json={"token": "secret-token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get("/api/auth/me").json() == {"authenticated": True}


def test_login_rejects_invalid_token(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post("/api/auth/login", json={"token": "wrong"})

    assert response.status_code == 401
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_dev_auth_bypass_marks_session_authenticated_without_cookie(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUPER_PERSONAL_RELOAD", "1")
    monkeypatch.setenv("SUPER_PERSONAL_DEV_AUTH_BYPASS", "1")
    client = make_client(tmp_path)

    assert client.get("/api/auth/me").json() == {"authenticated": True}
    assert client.post("/api/auth/login", json={"token": "wrong"}).status_code == 200


def test_login_uses_current_workspace_config_token_without_restart(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n",
        encoding="utf-8",
    )
    client = make_client(tmp_path)

    assert client.post("/api/auth/login", json={"token": "secret-token"}).status_code == 200

    config_path.write_text(
        "auth:\n  token: 1b55aa21-4359-447f-98c8-b6154c86f8b9\n"
        "proxy:\n  upstream_base_url: http://example.test/\n",
        encoding="utf-8",
    )

    assert client.get("/api/auth/me").json() == {"authenticated": False}
    assert client.post("/api/auth/login", json={"token": "secret-token"}).status_code == 401
    response = client.post(
        "/api/auth/login",
        json={"token": "1b55aa21-4359-447f-98c8-b6154c86f8b9"},
    )

    assert response.status_code == 200
    assert client.get("/api/auth/me").json() == {"authenticated": True}


def test_logout_clears_session(tmp_path) -> None:
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_api_requests_are_written_to_unified_log_without_bodies(tmp_path) -> None:
    client = make_client(tmp_path)

    client.post("/api/auth/login", json={"token": "secret-token"})
    client.get("/api/auth/me")

    log_files = list((tmp_path / "logs").glob("platform-*.log"))
    assert len(log_files) == 1
    log_content = log_files[0].read_text(encoding="utf-8")
    assert "request method=POST path=/api/auth/login status=200" in log_content
    assert "request method=GET path=/api/auth/me status=200" in log_content
    assert "secret-token" not in log_content
