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
    assert payload["path"].startswith("uploads/")
    assert ".." not in payload["path"]

    stored = settings.workspaces_dir / agent_id / payload["path"]
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


def _attachment_content(entries: list[tuple[str, int]], body: str = "看图") -> str:
    lines = ["附件已上传到工作区："]
    lines.extend(
        f"- {name}（工作区路径：uploads/{name}，大小：{size} 字节）"
        for name, size in entries
    )
    return "\n".join(lines) + "\n\n" + body


def test_build_model_content_accepts_legacy_absolute_upload_path(tmp_path) -> None:
    from app.services.attachments import build_model_content

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    data = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    (uploads / "old.png").write_bytes(data)
    content = (
        "附件已上传到工作区：\n"
        f"- old.png（工作区路径：/uploads/old.png，大小：{len(data)} 字节）"
    )

    built = build_model_content(content, tmp_path)
    assert isinstance(built, list)
    assert built[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_model_content_warns_about_oversized_image(tmp_path) -> None:
    from app.services.attachments import build_model_content

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    data = b"\x89PNG\r\n\x1a\n" + b"0" * 40
    (uploads / "big.png").write_bytes(data)
    content = _attachment_content([("big.png", len(data))])

    built = build_model_content(content, tmp_path, max_image_bytes=10)
    assert isinstance(built, str)
    assert "没有加入本次模型输入" in built
    assert "big.png" in built
    assert "超过单张" in built
    assert "无法压缩" in built
    assert "重新发送" in built


def test_build_model_content_warns_when_image_count_exceeded(tmp_path) -> None:
    from app.services.attachments import build_model_content

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    entries: list[tuple[str, int]] = []
    for index in range(5):
        name = f"p{index}.png"
        data = b"\x89PNG\r\n\x1a\n" + bytes([index]) * 16
        (uploads / name).write_bytes(data)
        entries.append((name, len(data)))
    content = _attachment_content(entries)

    built = build_model_content(content, tmp_path, max_images=2)
    assert isinstance(built, list)
    assert len([block for block in built if block["type"] == "image_url"]) == 2
    note = built[0]["text"]
    assert "超过单轮 2 张图片上限" in note
    assert "p4.png" in note


def test_build_model_content_ignores_non_image_attachments(tmp_path) -> None:
    from app.services.attachments import build_model_content

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    data = b"%PDF-1.4 hello"
    (uploads / "doc.pdf").write_bytes(data)
    content = _attachment_content([("doc.pdf", len(data))], body="读一下文件")

    built = build_model_content(content, tmp_path)
    assert built == content


def test_build_model_content_warns_about_unrecognized_image(tmp_path) -> None:
    from app.services.attachments import build_model_content

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    data = b"not an image"
    (uploads / "scan.png").write_bytes(data)
    content = _attachment_content([("scan.png", len(data))])

    built = build_model_content(content, tmp_path)
    assert isinstance(built, str)
    assert "图片格式无法识别" in built


def test_build_model_content_compresses_oversized_image(tmp_path) -> None:
    import base64
    import os
    from io import BytesIO

    from PIL import Image

    from app.services.attachments import build_model_content

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = Image.frombytes("RGB", (2200, 1400), os.urandom(2200 * 1400 * 3))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    data = buffer.getvalue()
    (uploads / "big.png").write_bytes(data)
    content = _attachment_content([("big.png", len(data))])

    built = build_model_content(content, tmp_path, max_image_bytes=200_000)
    assert isinstance(built, list)
    url = built[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    payload = base64.b64decode(url.split(",", 1)[1])
    assert len(payload) <= 200_000
    with Image.open(BytesIO(payload)) as compressed:
        assert max(compressed.size) <= 1600
    assert (uploads / "big.png").read_bytes() == data


def test_attachment_upload_rejects_non_active_agent_but_read_allows_history(
    client,
) -> None:
    agent = client.post("/api/agents", json={"name": "FilesPaused"}).json()
    uploaded = client.post(
        f"/api/agents/{agent['id']}/attachments",
        params={"filename": "note.txt"},
        content=b"hello",
    )
    assert uploaded.status_code == 201
    filename = uploaded.json()["path"].rsplit("/", 1)[-1]

    assert (
        client.put(
            f"/api/agents/{agent['id']}", json={"status": "paused"}
        ).status_code
        == 200
    )
    upload = client.post(
        f"/api/agents/{agent['id']}/attachments",
        params={"filename": "other.txt"},
        content=b"hello",
    )
    assert upload.status_code == 409
    assert "暂停或归档" in upload.json()["detail"]
    # Existing attachments are read-only history and stay viewable.
    read = client.get(f"/api/agents/{agent['id']}/uploads/{filename}")
    assert read.status_code == 200
    assert read.content == b"hello"

    assert client.delete(f"/api/agents/{agent['id']}").status_code == 200
    upload = client.post(
        f"/api/agents/{agent['id']}/attachments",
        params={"filename": "after-archive.txt"},
        content=b"hello",
    )
    assert upload.status_code == 409
    read = client.get(f"/api/agents/{agent['id']}/uploads/{filename}")
    assert read.status_code == 200
    assert read.content == b"hello"
