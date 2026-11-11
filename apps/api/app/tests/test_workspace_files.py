from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services.workspace_files import media_type_for, resolve_workspace_file


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
