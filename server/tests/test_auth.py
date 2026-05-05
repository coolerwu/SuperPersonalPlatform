from fastapi.testclient import TestClient

from server.infrastructure.config import AuthConfig, ProxyConfig, ServerConfig, Settings
from server.infrastructure.fastapi_app import create_app


def make_client() -> TestClient:
    settings = Settings(
        auth=AuthConfig(token="secret-token"),
        proxy=ProxyConfig(upstream_base_url="http://example.test/"),
        server=ServerConfig(),
    )
    return TestClient(create_app(settings))


def test_login_success_sets_authenticated_session() -> None:
    client = make_client()

    response = client.post("/api/auth/login", json={"token": "secret-token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get("/api/auth/me").json() == {"authenticated": True}


def test_login_rejects_invalid_token() -> None:
    client = make_client()

    response = client.post("/api/auth/login", json={"token": "wrong"})

    assert response.status_code == 401
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_logout_clears_session() -> None:
    client = make_client()
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert client.get("/api/auth/me").json() == {"authenticated": False}
