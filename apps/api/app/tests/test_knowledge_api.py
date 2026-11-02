from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from app.services.knowledge_sync import load_knowledge_settings, seed_knowledge_config


def _db_path() -> str:
    return settings.app_database_url.split("///", 1)[1]


@pytest.fixture
def knowledge_cleanup(client):
    def clear() -> None:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute("DELETE FROM knowledge_configs")
            conn.commit()

    clear()
    yield
    clear()


def test_knowledge_config_defaults(client, knowledge_cleanup) -> None:
    response = client.get("/api/knowledge")
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["enabled"] is False
    assert body["config"]["webdav_url"] == ""
    assert body["config"]["has_password"] is False
    assert body["config"]["local_dir"]
    assert body["status"]["enabled"] is False
    assert body["status"]["configured"] is False


def test_update_knowledge_config_hides_password(client, knowledge_cleanup) -> None:
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


def test_blank_password_keeps_and_clear_removes(client, knowledge_cleanup) -> None:
    client.put(
        "/api/knowledge",
        json={"webdav_url": "https://dav.example.com/dav/", "password": "secret"},
    )
    client.put("/api/knowledge", json={"username": "renamed"})
    assert client.get("/api/knowledge").json()["config"]["has_password"] is True

    client.put("/api/knowledge", json={"clear_password": True})
    assert client.get("/api/knowledge").json()["config"]["has_password"] is False


def test_invalid_webdav_url_rejected(client, knowledge_cleanup) -> None:
    response = client.put("/api/knowledge", json={"webdav_url": "ftp://example.com/dav"})
    assert response.status_code == 400
    assert "http" in response.json()["detail"]


def test_sync_endpoint_requires_config(client, knowledge_cleanup) -> None:
    assert client.post("/api/knowledge/sync").status_code == 400

    client.put(
        "/api/knowledge",
        json={"enabled": True, "webdav_url": "https://dav.example.com/dav/"},
    )
    response = client.post("/api/knowledge/sync")
    assert response.status_code == 200
    assert response.json()["queued"] is True


async def test_seed_knowledge_config_from_env(
    client, knowledge_cleanup, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "kb_webdav_url", "https://dav.example.com/dav/")
    monkeypatch.setattr(settings, "kb_webdav_username", "user")
    monkeypatch.setattr(settings, "kb_webdav_password", "pass")
    monkeypatch.setattr(settings, "kb_webdav_root", "/notes/")
    monkeypatch.setattr(settings, "kb_sync_interval_seconds", 300)

    await seed_knowledge_config()
    loaded = await load_knowledge_settings()
    assert loaded is not None
    assert loaded.webdav_url == "https://dav.example.com/dav/"
    assert loaded.username == "user"
    assert loaded.password == "pass"
    assert loaded.root == "notes"
    assert loaded.interval_seconds == 300

    await seed_knowledge_config()
    again = await load_knowledge_settings()
    assert again is not None
    assert again.signature == loaded.signature
