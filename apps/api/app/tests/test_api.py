from __future__ import annotations

import functools
import stat
import uuid

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.claude_runner import transcript_dir
from app.services.workspace import workspace_root


def test_auth_required() -> None:
    with TestClient(create_app(start_worker=False)) as client:
        assert client.get("/api/agents").status_code == 401


def test_default_agent_and_provider(client) -> None:
    agents = client.get("/api/agents")
    assert agents.status_code == 200
    payload = agents.json()
    assert payload
    assert payload[0]["name"] == "Assistant"
    assert payload[0]["provider_name"].lower() == "deepseek"
    assert payload[0]["provider_id"] == "default"
    assert "个人助手" in payload[0]["instructions"]
    assert "工作区" in payload[0]["instructions"]
    assert "保持直接" in payload[0]["instructions"]
    assert "简体中文" in payload[0]["instructions"]
    assert "SKILL.md" in payload[0]["instructions"]
    assert "技能创作" in payload[0]["instructions"]

    providers = client.get("/api/providers").json()
    assert providers[0]["id"] == "default"
    assert providers[0]["has_api_key"] is True


def test_new_agent_defaults_to_hermes_instructions(client) -> None:
    created = client.post("/api/agents", json={"name": "Default instructions"})
    assert created.status_code == 201
    instructions = created.json()["instructions"]
    assert "个人助手" in instructions
    assert "工作区" in instructions
    assert "简体中文" in instructions


def _provider_state(client):
    store = client.app.state.settings_store
    snapshot = client.portal.call(store.get)
    agent = next(item for item in snapshot.agents if item.id == "default")
    return store, agent.provider.model_dump(), agent.updated_at


def _restore_provider(client, provider: dict, updated_at: str | None) -> None:
    # The provider tests mutate shared settings; restore the seeded values so
    # later modules do not inherit "Backup".
    store = client.app.state.settings_store
    client.portal.call(
        functools.partial(
            store.update_agent_fields,
            "default",
            provider=provider,
            updated_at=updated_at,
        )
    )


def test_provider_create_and_system_info(client) -> None:
    _, original, original_updated = _provider_state(client)
    try:
        created = client.post(
            "/api/providers",
            json={
                "name": "Backup",
                "base_url": "https://api.example.com",
                "model": "example-model",
                "api_key": "secret",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["id"] == "default"
        assert body["name"] == "Backup"
        assert body["has_api_key"] is True
        assert "api_key" not in body

        providers = client.get("/api/providers").json()
        assert len(providers) == 1
        assert providers[0]["name"] == "Backup"

        system = client.get("/api/system")
        assert system.status_code == 200
        info = system.json()
        assert info["version"] == "0.1.0"
        assert info["execution_core"] == "claude-agent-sdk"
        assert info["database"] == "sqlite"
        assert info["capabilities"]["filesystem"] is True
        assert info["capabilities"]["skills"] is True
        assert info["capabilities"]["chat"] is True
        assert info["capabilities"]["memory"] is True
        assert info["skills_root"]
        assert info["counts"]["providers"] == 1
        assert info["counts"]["agents"] >= 1
        assert info["knowledge"]["enabled"] is False
        assert info["knowledge"]["local_dir"]
    finally:
        _restore_provider(client, original, original_updated)


def test_provider_update_preserves_or_replaces_key(client) -> None:
    _, original, original_updated = _provider_state(client)
    try:
        updated = client.put(
            "/api/providers/default",
            json={
                "name": "Rotated",
                "model": "example-model-2",
                "base_url": "https://api.other.example/v1",
                "api_key": "rotated-key",
            },
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["name"] == "Rotated"
        assert body["model"] == "example-model-2"
        assert body["base_url"] == "https://api.other.example/v1/anthropic"
        assert body["has_api_key"] is True

        blank = client.put("/api/providers/default", json={"api_key": ""})
        assert blank.status_code == 200
        assert blank.json()["has_api_key"] is True
        assert client.put("/api/providers/missing", json={}).status_code == 404
    finally:
        _restore_provider(client, original, original_updated)


def test_provider_create_rejects_blank_fields(client) -> None:
    response = client.post(
        "/api/providers",
        json={"name": "   ", "base_url": " ", "model": " "},
    )
    assert response.status_code == 400


def test_web_dist_and_spa_fallback(client) -> None:
    root = client.get("/")
    assert root.status_code == 200
    assert "Agent Command Center" in root.text
    assert root.headers["cache-control"] == "no-cache"

    spa = client.get("/chat")
    assert spa.status_code == 200
    assert "Agent Command Center" in spa.text

    assert client.get("/api/does-not-exist").status_code == 404


def test_timestamps_are_utc(client) -> None:
    created_at = client.get("/api/providers").json()[0]["created_at"]
    assert created_at.endswith("Z") or created_at.endswith("+00:00")


def test_agent_permanent_delete_removes_settings_and_directories(client) -> None:
    created = client.post("/api/agents", json={"name": "DeleteMe"}).json()
    agent_id = created["id"]
    workspace = workspace_root() / agent_id
    assert workspace.is_dir()
    projects = transcript_dir(agent_id)
    projects.mkdir(parents=True, exist_ok=True)
    (projects / f"{uuid.uuid4()}.jsonl").write_text("{}", encoding="utf-8")

    response = client.delete(f"/api/agents/{agent_id}/permanent")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["sessions"] == 1

    assert all(item["id"] != agent_id for item in client.get("/api/agents").json())
    assert not workspace.exists()
    assert not projects.exists()


def test_agent_permanent_delete_blocks_active_session(client) -> None:
    agent_id = client.post("/api/agents", json={"name": "Busy"}).json()["id"]
    projects = transcript_dir(agent_id)
    projects.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    (projects / f"{session_id}.jsonl").write_text("{}", encoding="utf-8")

    class _BusyRegistry:
        def is_busy(self, candidate: str) -> bool:
            return candidate == session_id

    client.app.state.registry = _BusyRegistry()
    try:
        response = client.delete(f"/api/agents/{agent_id}/permanent")
        assert response.status_code == 409
    finally:
        client.app.state.registry = None
        client.delete(f"/api/agents/{agent_id}/permanent")


def test_agent_permanent_delete_removes_project_directory(client) -> None:
    agent_id = client.post("/api/agents", json={"name": "CheckpointMe"}).json()["id"]
    projects = transcript_dir(agent_id)
    projects.mkdir(parents=True, exist_ok=True)
    (projects / f"{uuid.uuid4()}.jsonl").write_text("{}", encoding="utf-8")

    response = client.delete(f"/api/agents/{agent_id}/permanent")
    assert response.status_code == 200
    assert not projects.exists()


def test_memory_file_roundtrip(client) -> None:
    response = client.put("/api/memories", json={"content": "# 我\n- 偏好中文"})
    assert response.status_code == 200
    body = client.get("/api/memories").json()
    assert body["content"].startswith("# 我")
    assert body["content"].endswith("- 偏好中文")


def test_memory_file_is_private(client) -> None:
    from app.services.claude_env import claude_paths

    client.put("/api/memories", json={"content": "# 我\n"})
    path = claude_paths()["config"] / "CLAUDE.md"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_memory_file_rejects_non_string_content(client) -> None:
    response = client.put("/api/memories", json={"content": 123})
    assert response.status_code == 422


def test_memory_file_requires_content_field(client) -> None:
    response = client.put("/api/memories", json={})
    assert response.status_code == 422


def test_memory_file_write_keeps_backup(client) -> None:
    from app.services.claude_env import claude_paths

    client.put("/api/memories", json={"content": "# 第一版\n"})
    client.put("/api/memories", json={"content": "# 第二版\n"})
    path = claude_paths()["config"] / "CLAUDE.md"
    assert path.read_text("utf-8") == "# 第二版\n"
    backup = path.with_name(path.name + ".bak")
    assert backup.read_text("utf-8") == "# 第一版\n"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_agent_auto_memory_flag(client) -> None:
    created = client.post(
        "/api/agents", json={"name": "NoMemory", "auto_memory": False}
    ).json()
    assert created["auto_memory"] is False

    updated = client.put(
        f"/api/agents/{created['id']}", json={"auto_memory": True}
    ).json()
    assert updated["auto_memory"] is True


def test_agent_and_provider_updates_close_cached_clients(client):
    calls = []

    class _Registry:
        async def close_agent_clients(self, agent_id: str) -> int:
            calls.append(agent_id)
            return 0

    created = client.post("/api/agents", json={"name": "Cache"}).json()
    client.app.state.registry = _Registry()
    try:
        updated = client.put(
            f"/api/agents/{created['id']}", json={"instructions": "新指令"}
        )
        assert updated.status_code == 200
        assert calls == [created["id"]]

        _, original, original_updated = _provider_state(client)
        try:
            response = client.put(
                "/api/providers/default", json={"model": "example-model-3"}
            )
            assert response.status_code == 200
        finally:
            _restore_provider(client, original, original_updated)
        assert calls == [created["id"], "default"]
    finally:
        client.app.state.registry = None
        client.delete(f"/api/agents/{created['id']}/permanent")
