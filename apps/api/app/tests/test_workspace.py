from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db import _backfill_agent_workspaces, _ensure_agent_workspace_columns
from app.services.executor import DeepAgentExecutor, build_backend


def _create_agent(client, name: str) -> dict:
    response = client.post("/api/agents", json={"name": name, "instructions": "test"})
    assert response.status_code == 201
    return response.json()


def test_agent_workspace_defaults_to_auto_directory(client) -> None:
    agent = _create_agent(client, "Workspace default")
    assert agent["workspace_mode"] == "virtual"
    assert agent["workspace_path"] == agent["id"]
    assert (settings.workspaces_dir / agent["id"]).is_dir()


def test_agent_workspace_directory_is_created(client) -> None:
    agent = _create_agent(client, "Workspace directory")
    response = client.put(
        f"/api/agents/{agent['id']}",
        json={"workspace_mode": "directory", "workspace_path": "my-project"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspace_mode"] == "directory"
    assert body["workspace_path"] == "my-project"
    assert (settings.workspaces_dir / "my-project").is_dir()


def test_agent_workspace_partial_updates(client) -> None:
    agent = _create_agent(client, "Workspace partial")
    agent_id = agent["id"]

    switched = client.put(
        f"/api/agents/{agent_id}", json={"workspace_mode": "directory", "workspace_path": "partial"}
    ).json()
    assert switched["workspace_path"] == "partial"

    # Changing only the name leaves the workspace untouched.
    renamed = client.put(f"/api/agents/{agent_id}", json={"name": "Workspace partial 2"}).json()
    assert renamed["workspace_mode"] == "directory"
    assert renamed["workspace_path"] == "partial"

    # Switching back to virtual gives the agent its auto-managed directory again.
    reverted = client.put(f"/api/agents/{agent_id}", json={"workspace_mode": "virtual"}).json()
    assert reverted["workspace_mode"] == "virtual"
    assert reverted["workspace_path"] == agent_id
    assert (settings.workspaces_dir / agent_id).is_dir()
    assert (settings.workspaces_dir / "partial").is_dir()


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        "/tmp/escape",
        "~/escape",
        "../escape",
        "a/../b",
        "a\\b",
        "C:/escape",
        "C:\\escape",
        "a//b",
        ".",
        "a/./b",
        "a" * 201,
    ],
)
def test_agent_workspace_rejects_invalid_paths(client, path: str) -> None:
    agent = _create_agent(client, f"Workspace invalid {path[:12] or 'empty'}")
    response = client.put(
        f"/api/agents/{agent['id']}",
        json={"workspace_mode": "directory", "workspace_path": path},
    )
    assert response.status_code == 400, response.text


def test_agent_workspace_rejects_file_and_symlink_escape(client) -> None:
    agent = _create_agent(client, "Workspace escape")
    root = settings.workspaces_dir
    root.mkdir(parents=True, exist_ok=True)

    plain_file = root / "plain-file.txt"
    plain_file.write_text("not a directory")
    response = client.put(
        f"/api/agents/{agent['id']}",
        json={"workspace_mode": "directory", "workspace_path": "plain-file.txt"},
    )
    assert response.status_code == 400

    outside = Path(settings.app_data_dir) / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    link = root / "escape-link"
    link.unlink(missing_ok=True)
    link.symlink_to(outside)
    response = client.put(
        f"/api/agents/{agent['id']}",
        json={"workspace_mode": "directory", "workspace_path": "escape-link"},
    )
    assert response.status_code == 400


def test_agent_workspace_unicode(client) -> None:
    agent = _create_agent(client, "Workspace unicode")
    response = client.put(
        f"/api/agents/{agent['id']}",
        json={"workspace_mode": "directory", "workspace_path": "项目-α"},
    )
    assert response.status_code == 200, response.text
    assert (settings.workspaces_dir / "项目-α").is_dir()


async def test_workspace_columns_migration_and_backfill(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/legacy.db")
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE agents (id VARCHAR(32) PRIMARY KEY)")

    async with engine.begin() as conn:
        await _ensure_agent_workspace_columns(conn)
    async with engine.begin() as conn:
        await _ensure_agent_workspace_columns(conn)
        rows = (await conn.exec_driver_sql("PRAGMA table_info(agents)")).fetchall()
        columns = {row[1] for row in rows}
        assert {"workspace_mode", "workspace_path"} <= columns

        await conn.exec_driver_sql("INSERT INTO agents (id) VALUES ('legacy')")
        await _backfill_agent_workspaces(conn)
        values = (
            await conn.exec_driver_sql(
                "SELECT workspace_mode, workspace_path FROM agents WHERE id = 'legacy'"
            )
        ).fetchone()
        assert values == ("virtual", "legacy")
        assert (settings.workspaces_dir / "legacy").is_dir()

    await engine.dispose()


def test_build_backend_uses_workspace_directory() -> None:
    from deepagents.backends import FilesystemBackend

    backend = build_backend("backend-proj")
    assert isinstance(backend, FilesystemBackend)
    assert backend.virtual_mode is True
    assert backend.cwd == (settings.workspaces_dir / "backend-proj").resolve()


def test_workspace_backend_reads_and_writes_real_files() -> None:
    backend = build_backend("io-project")
    write = backend.write("/note.txt", "hello workspace")
    assert write.error is None
    saved = settings.workspaces_dir / "io-project" / "note.txt"
    assert saved.read_text() == "hello workspace"

    read = backend.read("/note.txt")
    assert read.error is None
    assert read.file_data is not None
    assert read.file_data["content"] == "hello workspace"


async def test_executor_invalid_workspace_fails_without_calling_model(tmp_path: Path) -> None:
    provider = SimpleNamespace(api_key="test-key", base_url="http://127.0.0.1:9", model="test-model")
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="../escape",
    )
    events = [event async for event in executor.run(instructions="x", session_id="s-1", content="hi")]
    assert events
    assert events[0].type == "run_failed"
    assert "workspace" in str(events[0].payload["error"]["message"])
