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


def test_uploaded_file_is_served_with_auth_and_content_type(client) -> None:
    agent_id = _agent_id(client)
    upload = client.post(
        f"/api/agents/{agent_id}/attachments",
        params={"filename": "photo.png"},
        content=b"\x89PNG\r\n\x1a\nhello",
    )
    assert upload.status_code == 201
    filename = upload.json()["path"].rsplit("/", 1)[-1]
    response = client.get(f"/api/agents/{agent_id}/uploads/{filename}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n\x1a\nhello"


def test_uploaded_file_route_rejects_traversal_and_missing_files(client) -> None:
    agent_id = _agent_id(client)
    assert client.get(f"/api/agents/{agent_id}/uploads/..%2Fapp.db").status_code == 404
    assert client.get(f"/api/agents/{agent_id}/uploads/missing.png").status_code == 404
    assert client.get("/api/agents/missing/uploads/a.png").status_code == 404


def test_uploaded_file_route_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as unauthenticated:
        response = unauthenticated.get("/api/agents/default/uploads/a.png")
        assert response.status_code == 401


def test_title_from_attachment_only_content_uses_the_file_name() -> None:
    from app.services.attachments import title_from_content

    content = (
        "附件已上传到工作区：\n"
        "- 报价单.pdf（工作区路径：/uploads/abc-报价单.pdf，大小：12 字节）"
    )
    assert title_from_content(content) == "报价单.pdf"


def test_build_model_content_attaches_images_only(client) -> None:
    from app.services.attachments import build_model_content

    agent_id = _agent_id(client)
    image = client.post(
        f"/api/agents/{agent_id}/attachments",
        params={"filename": "photo.png"},
        content=b"\x89PNG\r\n\x1a\n" + b"0" * 20,
    ).json()
    note = client.post(
        f"/api/agents/{agent_id}/attachments",
        params={"filename": "note.txt"},
        content=b"hello",
    ).json()
    content = (
        "附件已上传到工作区：\n"
        f"- {image['name']}（工作区路径：{image['path']}，大小：{image['size']} 字节）\n"
        f"- {note['name']}（工作区路径：{note['path']}，大小：{note['size']} 字节）\n\n"
        "看看这张图"
    )
    built = build_model_content(content, settings.workspaces_dir / agent_id)
    assert isinstance(built, list)
    assert built[0] == {"type": "text", "text": content}
    assert built[1]["type"] == "image_url"
    assert built[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert len(built) == 2
