from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app


def _agent_id(client) -> str:
    return client.get("/api/agents").json()[0]["id"]


def test_attachment_upload_writes_to_workspace(client) -> None:
    agent_id = _agent_id(client)
    response = client.post(
        f"/api/agents/{agent_id}/attachments",
        params={"filename": "../备注.txt"},
        content=b"hello",
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "备注.txt"
    assert payload["size"] == 5
    assert payload["path"].startswith("/uploads/")
    assert ".." not in payload["path"]

    stored = settings.workspaces_dir / agent_id / payload["path"].lstrip("/")
    assert stored.read_bytes() == b"hello"
    assert stored.parent.name == "uploads"


def test_attachment_rejects_empty_body(client) -> None:
    agent_id = _agent_id(client)
    response = client.post(
        f"/api/agents/{agent_id}/attachments",
        params={"filename": "empty.txt"},
        content=b"",
    )
    assert response.status_code == 400


def test_attachment_rejects_large_body(client, monkeypatch) -> None:
    monkeypatch.setattr("app.routers.agents.MAX_ATTACHMENT_BYTES", 4)
    agent_id = _agent_id(client)
    response = client.post(
        f"/api/agents/{agent_id}/attachments",
        params={"filename": "big.txt"},
        content=b"hello",
    )
    assert response.status_code == 413


def test_attachment_unknown_agent(client) -> None:
    response = client.post(
        "/api/agents/missing/attachments",
        params={"filename": "note.txt"},
        content=b"hello",
    )
    assert response.status_code == 404


def test_attachment_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as client:
        agent = client.get("/api/agents")
        assert agent.status_code == 401
        response = client.post(
            "/api/agents/default/attachments",
            params={"filename": "note.txt"},
            content=b"hello",
        )
        assert response.status_code == 401
