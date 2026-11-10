from __future__ import annotations

import time

from app.services.knowledge_sync import load_knowledge_settings
from app.services.settings_store import build_default_settings


def _reset(client) -> None:
    response = client.put(
        "/api/knowledge",
        json={
            "enabled": False,
            "webdav_url": "",
            "username": "",
            "root": "",
            "interval_seconds": 300,
            "clear_password": True,
        },
    )
    assert response.status_code == 200


def test_knowledge_config_defaults(client) -> None:
    _reset(client)
    response = client.get("/api/knowledge")
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["enabled"] is False
    assert body["config"]["webdav_url"] == ""
    assert body["config"]["has_password"] is False
    assert body["config"]["local_dir"]
    assert body["status"]["enabled"] is False
    assert body["status"]["configured"] is False


def test_update_knowledge_config_hides_password(client) -> None:
    _reset(client)
    response = client.put(
        "/api/knowledge",
        json={
            "enabled": True,
            "webdav_url": "https://dav.jianguoyun.com/dav/",
            "username": "me@example.com",
            "password": "secret",
            "root": "/知识库/",
            "interval_seconds": 600,
        },
    )
    assert response.status_code == 200
    config = response.json()["config"]
    assert config["has_password"] is True
    assert "password" not in config
    assert config["root"] == "知识库"
    assert config["interval_seconds"] == 600

    got = client.get("/api/knowledge").json()["config"]
    assert got["webdav_url"] == "https://dav.jianguoyun.com/dav/"
    assert got["username"] == "me@example.com"
    assert got["has_password"] is True
    _reset(client)


def test_blank_password_keeps_and_clear_removes(client) -> None:
    _reset(client)
    client.put(
        "/api/knowledge",
        json={"webdav_url": "https://dav.example.com/dav/", "password": "secret"},
    )
    client.put("/api/knowledge", json={"username": "renamed"})
    assert client.get("/api/knowledge").json()["config"]["has_password"] is True

    client.put("/api/knowledge", json={"clear_password": True})
    assert client.get("/api/knowledge").json()["config"]["has_password"] is False
    _reset(client)


def test_invalid_webdav_url_rejected(client) -> None:
    response = client.put("/api/knowledge", json={"webdav_url": "ftp://example.com/dav"})
    assert response.status_code == 400
    assert "http" in response.json()["detail"]


def test_sync_endpoint_requires_config(client) -> None:
    _reset(client)
    assert client.post("/api/knowledge/sync").status_code == 400

    client.put(
        "/api/knowledge",
        json={"enabled": True, "webdav_url": "https://dav.example.com/dav/"},
    )
    response = client.post("/api/knowledge/sync")
    assert response.status_code == 200
    assert response.json()["queued"] is True
    _reset(client)


def test_sync_endpoint_respects_rate_limit_backoff(client) -> None:
    _reset(client)
    client.put(
        "/api/knowledge",
        json={"enabled": True, "webdav_url": "https://dav.example.com/dav/"},
    )
    client.app.state.knowledge._blocked_until = time.monotonic() + 60

    response = client.post("/api/knowledge/sync")
    assert response.status_code == 429
    assert "限流" in response.json()["detail"]
    client.app.state.knowledge._blocked_until = 0.0
    _reset(client)


async def test_knowledge_settings_load_from_store(client) -> None:
    _reset(client)
    client.put(
        "/api/knowledge",
        json={
            "enabled": True,
            "webdav_url": "https://dav.example.com/dav/",
            "username": "user",
            "password": "pass",
            "root": "/notes/",
            "interval_seconds": 300,
        },
    )
    loaded = await load_knowledge_settings()
    assert loaded is not None
    assert loaded.webdav_url == "https://dav.example.com/dav/"
    assert loaded.username == "user"
    assert loaded.password == "pass"
    assert loaded.root == "notes"
    assert loaded.interval_seconds == 300
    _reset(client)


def test_default_settings_seed_knowledge_from_env(monkeypatch) -> None:
    monkeypatch.setenv("KB_WEBDAV_URL", "https://dav.example.com/dav/")
    monkeypatch.setenv("KB_WEBDAV_USERNAME", "user")
    monkeypatch.setenv("KB_WEBDAV_PASSWORD", "pass")
    monkeypatch.setenv("KB_WEBDAV_ROOT", "/notes/")

    data = build_default_settings()
    assert data["knowledge"]["url"] == "https://dav.example.com/dav/"
    assert data["knowledge"]["username"] == "user"
    assert data["knowledge"]["password"] == "pass"
    assert data["knowledge"]["root"] == "/notes/"
