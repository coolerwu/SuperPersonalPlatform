from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services.workspace_files import (
    media_type_for,
    preview_format,
    read_text_preview,
    resolve_workspace_file,
)


def _workspace(tmp_path, agent="default"):
    workspace = tmp_path / "workspaces" / agent
    workspace.mkdir(parents=True)
    return workspace


def test_resolves_a_file_inside_the_workspace(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "baidu.png").write_bytes(b"png")
    target = resolve_workspace_file("default", "baidu.png", home=tmp_path)
    assert target == (workspace / "baidu.png").resolve()


def test_resolves_nested_paths(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "uploads").mkdir()
    (workspace / "uploads" / "note.txt").write_text("x")
    target = resolve_workspace_file("default", "uploads/note.txt", home=tmp_path)
    assert target == (workspace / "uploads" / "note.txt").resolve()


def test_rejects_traversal_symlinks_and_missing_files(tmp_path):
    workspace = _workspace(tmp_path)
    (tmp_path / "secret.txt").write_text("secret")
    assert resolve_workspace_file("default", "../secret.txt", home=tmp_path) is None
    assert resolve_workspace_file("default", "/etc/passwd", home=tmp_path) is None
    assert resolve_workspace_file("missing", "x.txt", home=tmp_path) is None
    (workspace / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert resolve_workspace_file("default", "link.txt", home=tmp_path) is None
    assert resolve_workspace_file("default", "nope.txt", home=tmp_path) is None
    assert resolve_workspace_file("default", ".", home=tmp_path) is None


def test_media_type_marks_scriptable_files_as_downloads():
    assert media_type_for(Path("a.png")) == ("image/png", False)
    assert media_type_for(Path("a.pdf")) == ("application/pdf", False)
    assert media_type_for(Path("a.html")) == ("application/octet-stream", True)
    assert media_type_for(Path("a.svg")) == ("application/octet-stream", True)
    assert media_type_for(Path("a.js")) == ("application/octet-stream", True)


def test_workspace_file_is_served_inline(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\nabc")
    response = client.get(f"/api/agents/{agent_id}/files/shot.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n\x1a\nabc"


def test_workspace_file_route_forces_download_for_html(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "report.html").write_text("<script>alert(1)</script>")
    response = client.get(f"/api/agents/{agent_id}/files/report.html")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_workspace_file_route_rejects_traversal_and_unknown_agents(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    assert client.get(f"/api/agents/{agent_id}/files/missing.png").status_code == 404
    assert (
        client.get(
            f"/api/agents/{agent_id}/files/..%2F..%2Fdata%2Fsettings.json"
        ).status_code
        == 404
    )
    assert client.get("/api/agents/missing/files/a.png").status_code == 404


def test_workspace_file_route_requires_auth():
    with TestClient(create_app(start_worker=False)) as unauthenticated:
        response = unauthenticated.get("/api/agents/default/files/a.png")
        assert response.status_code == 401


def test_preview_format_recognizes_markdown_and_text():
    assert preview_format(Path("a.md")) == "markdown"
    assert preview_format(Path("a.MARKDOWN")) == "markdown"
    assert preview_format(Path("a.py")) == "text"
    assert preview_format(Path("a.json")) == "text"
    assert preview_format(Path("a.svg")) == "text"
    assert preview_format(Path("a.pdf")) is None
    assert preview_format(Path("a.zip")) is None


def test_read_text_preview_returns_content_and_size(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("# 标题\n第二行", "utf-8")
    content, truncated, size = read_text_preview(target)
    assert content == "# 标题\n第二行"
    assert truncated is False
    assert size == target.stat().st_size


def test_read_text_preview_truncates_at_512_kb(tmp_path):
    target = tmp_path / "big.log"
    target.write_bytes(b"x" * (512 * 1024 + 10))
    content, truncated, size = read_text_preview(target)
    assert truncated is True
    assert len(content) == 512 * 1024
    assert size == 512 * 1024 + 10


def test_read_text_preview_replaces_invalid_utf8_and_strips_bom(tmp_path):
    target = tmp_path / "bad.txt"
    target.write_bytes(b"\xef\xbb\xbfok\xffbad")
    content, truncated, size = read_text_preview(target)
    assert content == "ok�bad"
    assert truncated is False
    assert size == 9


LIMIT = 512 * 1024


@pytest.mark.parametrize(
    ("extra", "expected_truncated", "expected_len"),
    [(-1, False, LIMIT - 1), (0, False, LIMIT), (1, True, LIMIT)],
)
def test_read_text_preview_at_the_truncation_boundary(
    tmp_path, extra, expected_truncated, expected_len
):
    target = tmp_path / "edge.log"
    target.write_bytes(b"x" * (LIMIT + extra))
    content, truncated, _ = read_text_preview(target)
    assert truncated is expected_truncated
    assert len(content) == expected_len


def test_read_text_preview_multibyte_split_only_adds_replacement(tmp_path):
    target = tmp_path / "split.txt"
    target.write_bytes(b"a" * (LIMIT - 1) + "中".encode("utf-8") + b"tail")
    content, truncated, _ = read_text_preview(target)
    assert truncated is True
    assert content.endswith("�")
    # The cap is on raw bytes read; one split sequence can re-encode 1-3
    # consumed bytes as 3, so the decoded string can exceed LIMIT by at most 2.
    assert len(content.encode("utf-8")) <= LIMIT + 2


def test_file_preview_returns_markdown_content(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "report.md").write_text("# 标题\n正文", "utf-8")
    response = client.get(f"/api/agents/{agent_id}/file-preview/report.md")
    assert response.status_code == 200
    assert response.json() == {
        "content": "# 标题\n正文",
        "format": "markdown",
        "truncated": False,
        "size": len("# 标题\n正文".encode()),
    }
    assert response.headers["cache-control"] == "private, no-cache"


def test_file_preview_returns_plain_text_for_code(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "run.py").write_text("print('hi')\n", "utf-8")
    response = client.get(f"/api/agents/{agent_id}/file-preview/run.py")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "text"
    assert body["content"] == "print('hi')\n"


def test_file_preview_truncates_large_text(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "big.log").write_bytes(b"x" * (512 * 1024 + 1))
    response = client.get(f"/api/agents/{agent_id}/file-preview/big.log")
    body = response.json()
    assert response.status_code == 200
    assert body["truncated"] is True
    assert len(body["content"]) == 512 * 1024
    assert body["size"] == 512 * 1024 + 1


def test_file_preview_rejects_binary_types(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "doc.pdf").write_bytes(b"%PDF-1.4")
    (workspace / "bundle.zip").write_bytes(b"PK\x03\x04")
    for name in ("doc.pdf", "bundle.zip"):
        response = client.get(f"/api/agents/{agent_id}/file-preview/{name}")
        assert response.status_code == 415
        assert response.json() == {"detail": "file type cannot be previewed"}
        assert response.headers["cache-control"] == "no-store"


def test_file_preview_rejects_missing_and_traversal(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    missing = client.get(f"/api/agents/{agent_id}/file-preview/missing.md")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "file not found"}
    assert missing.headers["cache-control"] == "no-store"

    traversal = client.get(
        f"/api/agents/{agent_id}/file-preview/..%2F..%2Fdata%2Fsettings.json"
    )
    assert traversal.status_code == 404
    assert traversal.json() == {"detail": "file not found"}

    unknown = client.get("/api/agents/missing/file-preview/a.md")
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "agent not found"}


def test_file_preview_allows_paused_agent(client):
    agent = client.post("/api/agents", json={"name": "PausedPreview"}).json()
    assert (
        client.put(
            f"/api/agents/{agent['id']}", json={"status": "paused"}
        ).status_code
        == 200
    )
    workspace = settings.workspaces_dir / agent["id"]
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "note.txt").write_text("still readable", "utf-8")
    response = client.get(f"/api/agents/{agent['id']}/file-preview/note.txt")
    assert response.status_code == 200
    assert response.json()["content"] == "still readable"


def test_file_preview_requires_auth():
    with TestClient(create_app(start_worker=False)) as unauthenticated:
        response = unauthenticated.get(
            "/api/agents/default/file-preview/a.md"
        )
        assert response.status_code == 401


def test_file_preview_allows_archived_agent(client):
    agent = client.post("/api/agents", json={"name": "ArchivedPreview"}).json()
    assert client.delete(f"/api/agents/{agent['id']}").status_code == 200
    workspace = settings.workspaces_dir / agent["id"]
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "old.txt").write_text("archived but readable", "utf-8")
    response = client.get(f"/api/agents/{agent['id']}/file-preview/old.txt")
    assert response.status_code == 200
    assert response.json()["content"] == "archived but readable"


def test_file_preview_maps_read_errors_to_404(client, monkeypatch):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "race.md").write_text("x", "utf-8")

    def boom(_path):
        raise FileNotFoundError("gone")

    monkeypatch.setattr("app.routers.agents.read_text_preview", boom)
    response = client.get(f"/api/agents/{agent_id}/file-preview/race.md")
    assert response.status_code == 404
    assert response.json() == {"detail": "file not found"}
