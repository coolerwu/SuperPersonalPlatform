from fastapi.testclient import TestClient

from app.main import create_app


def test_auth_required() -> None:
    with TestClient(create_app(start_worker=False)) as client:
        assert client.get("/api/agents").status_code == 401


def test_default_agent_and_provider(client) -> None:
    agents = client.get("/api/agents")
    assert agents.status_code == 200
    payload = agents.json()
    assert payload
    assert payload[0]["name"] == "Assistant"
    assert payload[0]["provider_name"] == "DeepSeek"
    assert "保持直接" in payload[0]["instructions"]
    assert "只支持聊天" in payload[0]["instructions"]
    assert "简体中文" in payload[0]["instructions"]

    providers = client.get("/api/providers").json()
    assert providers[0]["id"] == "default"
    assert providers[0]["has_api_key"] is True


def test_agent_and_session_isolation(client) -> None:
    first_agent = client.get("/api/agents").json()[0]["id"]
    created = client.post("/api/agents", json={"name": "Second", "instructions": "Be terse."})
    assert created.status_code == 201
    second_agent = created.json()["id"]

    first_session = client.post(f"/api/agents/{first_agent}/sessions", json={}).json()
    second_session = client.post(f"/api/agents/{second_agent}/sessions", json={}).json()

    first_list = client.get(f"/api/agents/{first_agent}/sessions").json()
    second_list = client.get(f"/api/agents/{second_agent}/sessions").json()
    assert {item["id"] for item in first_list} == {first_session["id"]}
    assert {item["id"] for item in second_list} == {second_session["id"]}


def test_chat_creates_run_and_enforces_one_active_run(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]

    response = client.post("/api/chat/messages", json={"session_id": session_id, "content": "hello"})
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    run = client.get(f"/api/runs/{run_id}")
    assert run.status_code == 200
    assert run.json()["status"] == "queued"

    events = client.get(f"/api/runs/{run_id}/events").json()
    assert events[0]["type"] == "run_queued"

    conflict = client.post("/api/chat/messages", json={"session_id": session_id, "content": "again"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["active_run_id"] == run_id

    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [message["role"] for message in messages] == ["user"]
    assert messages[0]["run_id"] == run_id


def test_provider_create_and_system_info(client) -> None:
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
    assert body["name"] == "Backup"
    assert body["has_api_key"] is True
    assert "api_key" not in body

    system = client.get("/api/system")
    assert system.status_code == 200
    info = system.json()
    assert info["version"] == "0.1.0"
    assert info["database"] == "sqlite"
    assert info["capabilities"]["filesystem"] is False
    assert info["capabilities"]["chat"] is True
    assert info["capabilities"]["memory"] is True
    assert info["counts"]["providers"] >= 2
    assert info["counts"]["agents"] >= 1


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


def test_agent_permanent_delete_cascades_sessions(client) -> None:
    agent_id = client.post("/api/agents", json={"name": "DeleteMe"}).json()["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]

    response = client.delete(f"/api/agents/{agent_id}/permanent")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["sessions"] == 1

    assert all(item["id"] != agent_id for item in client.get("/api/agents").json())
    assert client.get(f"/api/sessions/{session_id}/messages").status_code == 404


def test_agent_permanent_delete_blocks_active_run(client) -> None:
    agent_id = client.post("/api/agents", json={"name": "Busy"}).json()["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    client.post("/api/chat/messages", json={"session_id": session_id, "content": "hi"})

    response = client.delete(f"/api/agents/{agent_id}/permanent")
    assert response.status_code == 409


def test_retry_requires_failed_run(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    run_id = client.post(
        "/api/chat/messages", json={"session_id": session_id, "content": "retry me"}
    ).json()["run_id"]

    response = client.post(f"/api/runs/{run_id}/retry")
    assert response.status_code == 409


def test_agent_permanent_delete_removes_checkpoint(client) -> None:
    from app.config import settings

    agent_id = client.post("/api/agents", json={"name": "CheckpointMe"}).json()["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    checkpoint = settings.checkpoint_dir / f"{session_id}.sqlite"
    checkpoint.write_bytes(b"checkpoint")

    response = client.delete(f"/api/agents/{agent_id}/permanent")
    assert response.status_code == 200
    assert not checkpoint.exists()


def test_memory_crud_and_dedupe(client) -> None:
    created = client.post(
        "/api/memories",
        json={"scope": "user", "content": "回答先给结论", "tags": ["style"]},
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    duplicate = client.post(
        "/api/memories",
        json={"scope": "user", "content": "回答先给结论", "tags": ["style", "preference"]},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == memory_id

    listed = client.get("/api/memories?scope=user").json()
    assert len([item for item in listed if item["content"] == "回答先给结论"]) == 1

    updated = client.put(
        f"/api/memories/{memory_id}",
        json={"content": "回答先给结论，再展开", "tags": ["style", "preference"]},
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "回答先给结论，再展开"

    deleted = client.delete(f"/api/memories/{memory_id}")
    assert deleted.status_code == 200
    assert client.delete(f"/api/memories/{memory_id}").status_code == 404


def test_agent_memory_requires_agent(client) -> None:
    response = client.post(
        "/api/memories", json={"scope": "agent", "content": "这个 Agent 负责交易分析"}
    )
    assert response.status_code == 400

    agent_id = client.get("/api/agents").json()[0]["id"]
    created = client.post(
        "/api/memories",
        json={
            "scope": "agent",
            "agent_id": agent_id,
            "content": "这个 Agent 负责交易分析",
        },
    )
    assert created.status_code == 201
    listed = client.get(f"/api/memories?scope=agent&agent_id={agent_id}").json()
    assert any(item["id"] == created.json()["id"] for item in listed)


def test_explicit_remember_creates_user_memory(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    response = client.post(
        "/api/chat/messages",
        json={"session_id": session_id, "content": "记住：以后回答先给结论"},
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    memories = client.get("/api/memories?scope=user").json()
    assert any("以后回答先给结论" in item["content"] for item in memories)

    events = client.get(f"/api/runs/{run_id}/events").json()
    assert any(
        event["type"] == "memory_saved"
        and event["payload"].get("source") == "explicit"
        for event in events
    )


def test_agent_auto_memory_flag(client) -> None:
    created = client.post(
        "/api/agents", json={"name": "NoMemory", "auto_memory": False}
    ).json()
    assert created["auto_memory"] is False

    updated = client.put(
        f"/api/agents/{created['id']}", json={"auto_memory": True}
    ).json()
    assert updated["auto_memory"] is True


def test_parse_extraction_handles_fenced_json() -> None:
    from app.services.memory_service import parse_extraction

    result = parse_extraction(
        '```json\n{"memories":[{"content":"用户偏好中文","tags":["preference"]}]}\n```'
    )
    assert result == [
        {"action": "add", "id": "", "content": "用户偏好中文", "tags": ["preference"]}
    ]

    operations = parse_extraction(
        '{"memories":['
        '{"action":"update","id":"mem_a","content":"用户偏好先给结论","tags":["style"]},'
        '{"action":"delete","id":"mem_b"}'
        "]}"
    )
    assert operations[0]["action"] == "update"
    assert operations[0]["id"] == "mem_a"
    assert operations[1] == {"action": "delete", "id": "mem_b", "content": "", "tags": []}


def test_fts_query_builds_or_terms() -> None:
    from app.services.memory_service import fts_query

    query = fts_query("用户偏好，回答简洁")
    assert '"用户偏好"' in query
    assert '"回答简洁"' in query
    assert " OR " in query
