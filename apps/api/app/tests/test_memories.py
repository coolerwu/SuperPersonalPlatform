from __future__ import annotations

import os
import stat
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.claude_env import claude_paths


def _create_agent(client, name: str = "Memory Agent") -> str:
    response = client.post("/api/agents", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _agent_memory_path(agent_id: str) -> Path:
    return claude_paths()["workspaces"] / agent_id / "CLAUDE.md"


def _auto_memory_dir(agent_id: str) -> Path:
    return claude_paths()["config"] / "projects" / agent_id / "memory"


def test_memories_api_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.get("/api/memories/agents/abc").status_code == 401


def test_agent_memory_roundtrip_and_backup(client) -> None:
    agent_id = _create_agent(client)
    assert (
        client.get(f"/api/memories/agents/{agent_id}").json()["content"] == ""
    )

    first = client.put(
        f"/api/memories/agents/{agent_id}", json={"content": "# 第一版\n"}
    )
    assert first.status_code == 200
    assert first.json()["content"] == "# 第一版\n"
    assert first.json()["saved"] is True

    second = client.put(
        f"/api/memories/agents/{agent_id}", json={"content": "# 第二版\n"}
    )
    assert second.status_code == 200
    assert (
        client.get(f"/api/memories/agents/{agent_id}").json()["content"]
        == "# 第二版\n"
    )

    path = _agent_memory_path(agent_id)
    assert path.read_text("utf-8") == "# 第二版\n"
    backup = path.with_name(path.name + ".bak")
    assert backup.read_text("utf-8") == "# 第一版\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_agent_memory_is_isolated_per_agent(client) -> None:
    first = _create_agent(client, "First")
    second = _create_agent(client, "Second")
    client.put(f"/api/memories/agents/{first}", json={"content": "first"})
    client.put(f"/api/memories/agents/{second}", json={"content": "second"})

    assert (
        client.get(f"/api/memories/agents/{first}").json()["content"] == "first"
    )
    assert (
        client.get(f"/api/memories/agents/{second}").json()["content"]
        == "second"
    )


def test_agent_memory_rejects_unknown_agent(client) -> None:
    assert (
        client.get(
            "/api/memories/agents/00000000000000000000000000000000"
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/memories/agents/00000000000000000000000000000000",
            json={"content": "x"},
        ).status_code
        == 404
    )
    assert (
        client.get("/api/memories/agents/bad%20id").status_code == 404
    )


def test_auto_memory_lists_markdown_files(client) -> None:
    agent_id = _create_agent(client)
    directory = _auto_memory_dir(agent_id)
    directory.mkdir(parents=True)
    (directory / "MEMORY.md").write_text("- index\n", encoding="utf-8")
    (directory / "b-topic.md").write_text("b\n", encoding="utf-8")
    (directory / "a-topic.md").write_text("a\n", encoding="utf-8")
    (directory / "ignored.txt").write_text("no\n", encoding="utf-8")
    outside = directory.parent / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    os.symlink(outside, directory / "link.md")

    response = client.get(f"/api/memories/agents/{agent_id}/auto-memory")

    assert response.status_code == 200
    body = response.json()
    assert body["directory"].endswith(f"projects/{agent_id}/memory")
    assert [item["name"] for item in body["files"]] == [
        "MEMORY.md",
        "a-topic.md",
        "b-topic.md",
    ]
    assert body["files"][0]["content"] == "- index\n"
    assert body["files"][1]["size"] == 2
    assert body["files"][1]["updated_at"]
    assert body["errors"] == []


def test_auto_memory_empty_directory(client) -> None:
    agent_id = _create_agent(client)
    body = client.get(f"/api/memories/agents/{agent_id}/auto-memory").json()
    assert body["files"] == []
    assert body["errors"] == []


def test_agent_memory_rejects_symlink_document(client) -> None:
    agent_id = _create_agent(client)
    path = _agent_memory_path(agent_id)
    outside = path.parent / "outside-secret.md"
    outside.write_text("secret\n", encoding="utf-8")
    path.symlink_to(outside)

    assert client.get(f"/api/memories/agents/{agent_id}").status_code == 409
    assert (
        client.put(
            f"/api/memories/agents/{agent_id}", json={"content": "x"}
        ).status_code
        == 409
    )
    assert outside.read_text("utf-8") == "secret\n"


def test_agent_memory_works_for_archived_agent(client) -> None:
    agent_id = _create_agent(client)
    assert client.delete(f"/api/agents/{agent_id}").status_code == 200

    response = client.put(
        f"/api/memories/agents/{agent_id}", json={"content": "archived"}
    )
    assert response.status_code == 200
    assert (
        client.get(f"/api/memories/agents/{agent_id}").json()["content"]
        == "archived"
    )
    assert (
        client.get(
            f"/api/memories/agents/{agent_id}/auto-memory"
        ).status_code
        == 200
    )


def test_auto_memory_rejects_symlinked_directory(client) -> None:
    agent_id = _create_agent(client)
    target = claude_paths()["config"] / "projects" / agent_id / "real-memory"
    target.mkdir(parents=True)
    (target / "MEMORY.md").write_text("hidden\n", encoding="utf-8")
    (claude_paths()["config"] / "projects" / agent_id / "memory").symlink_to(
        target
    )

    body = client.get(f"/api/memories/agents/{agent_id}/auto-memory").json()

    assert body["files"] == []
    assert any("符号链接" in error for error in body["errors"])


def test_auto_memory_caps_error_list(client) -> None:
    agent_id = _create_agent(client)
    directory = _auto_memory_dir(agent_id)
    directory.mkdir(parents=True)
    for index in range(25):
        (directory / f"huge-{index}.md").write_text(
            "x" * (129 * 1024), encoding="utf-8"
        )

    body = client.get(f"/api/memories/agents/{agent_id}/auto-memory").json()

    assert body["files"] == []
    assert len(body["errors"]) == 21
    assert body["errors"][-1].startswith("还有 5 条提示未显示")


def test_auto_memory_caps_file_count(client) -> None:
    agent_id = _create_agent(client)
    directory = _auto_memory_dir(agent_id)
    directory.mkdir(parents=True)
    for index in range(205):
        (directory / f"note-{index:03d}.md").write_text("x\n", encoding="utf-8")

    body = client.get(f"/api/memories/agents/{agent_id}/auto-memory").json()

    assert len(body["files"]) == 200
    assert any("文件数量超过" in error for error in body["errors"])


def test_auto_memory_caps_total_bytes(client) -> None:
    agent_id = _create_agent(client)
    directory = _auto_memory_dir(agent_id)
    directory.mkdir(parents=True)
    for index in range(9):
        (directory / f"block-{index}.md").write_text(
            "x" * (120 * 1024), encoding="utf-8"
        )

    body = client.get(f"/api/memories/agents/{agent_id}/auto-memory").json()

    assert sum(item["size"] for item in body["files"]) <= 1024 * 1024
    assert any("总展示量" in error for error in body["errors"])


def test_auto_memory_skips_oversized_files(client) -> None:
    agent_id = _create_agent(client)
    directory = _auto_memory_dir(agent_id)
    directory.mkdir(parents=True)
    (directory / "MEMORY.md").write_text("ok\n", encoding="utf-8")
    (directory / "huge.md").write_text(
        "x" * (129 * 1024), encoding="utf-8"
    )

    body = client.get(f"/api/memories/agents/{agent_id}/auto-memory").json()

    assert [item["name"] for item in body["files"]] == ["MEMORY.md"]
    assert any("huge.md" in error for error in body["errors"])
