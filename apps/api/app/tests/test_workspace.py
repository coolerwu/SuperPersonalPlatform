from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.config import settings
from app.services.executor import DeepAgentExecutor, build_backend


def _create_agent(client, name: str) -> dict:
    response = client.post("/api/agents", json={"name": name, "instructions": "test"})
    assert response.status_code == 201
    return response.json()


def test_agent_workspace_is_agent_id_directory(client) -> None:
    agent = _create_agent(client, "Workspace agent")
    assert (settings.workspaces_dir / agent["id"]).is_dir()


def test_agent_out_does_not_expose_workspace_fields(client) -> None:
    agent = _create_agent(client, "Workspace fields")
    assert "workspace_mode" not in agent
    assert "workspace_path" not in agent


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
