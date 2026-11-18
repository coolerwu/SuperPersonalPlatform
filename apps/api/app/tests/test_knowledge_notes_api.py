from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services.knowledge_notes import NoteReadError, reset_index_cache


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    root = tmp_path / "knowledge"
    root.mkdir()
    monkeypatch.setattr(settings, "app_knowledge_dir", root)
    monkeypatch.setattr(settings, "app_data_dir", tmp_path / "data")
    reset_index_cache()
    yield root
    reset_index_cache()


def test_random_note_returns_content(client, notes_dir):
    (notes_dir / "note.md").write_text("# 标题\n正文", encoding="utf-8")
    response = client.get("/api/knowledge/notes/random")
    assert response.status_code == 200
    body = response.json()
    assert body["note"] == {
        "path": "note.md",
        "title": "标题",
        "content": "# 标题\n正文",
        "size": len("# 标题\n正文".encode("utf-8")),
    }
    assert body["meta"] == {"seen": 1, "total": 1, "notice": ""}


def test_consecutive_draws_differ(client, notes_dir):
    (notes_dir / "a.md").write_text("# A", encoding="utf-8")
    (notes_dir / "b.md").write_text("# B", encoding="utf-8")
    first = client.get("/api/knowledge/notes/random").json()["note"]["path"]
    second = client.get(
        "/api/knowledge/notes/random", params={"exclude": first}
    ).json()["note"]["path"]
    assert {first, second} == {"a.md", "b.md"}


def test_random_note_empty_knowledge(client, notes_dir):
    response = client.get("/api/knowledge/notes/random")
    assert response.status_code == 404
    assert "知识库没有可用的笔记" in response.json()["detail"]


def test_random_note_requires_auth():
    with TestClient(create_app(start_worker=False)) as unauthenticated:
        assert unauthenticated.get("/api/knowledge/notes/random").status_code == 401


def test_random_note_read_error_maps_to_500(client, monkeypatch):
    async def fake_draw(exclude):
        raise NoteReadError("boom")

    monkeypatch.setattr("app.routers.knowledge.draw_random_note", fake_draw)
    response = client.get("/api/knowledge/notes/random")
    assert response.status_code == 500
    assert "读取笔记失败" in response.json()["detail"]
