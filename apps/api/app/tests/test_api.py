import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import create_app
from app.models import Message, Run, Session, utcnow
from app.services.run_service import complete_run


def _set_run_running(run_id: str) -> None:
    async def update() -> None:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            assert run is not None
            run.status = "running"
            run.started_at = utcnow()
            await db.commit()

    asyncio.run(update())


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


def test_session_list_orders_by_last_message_time(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    older = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    newer = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    base = utcnow()

    def seed(session_id: str, message_at, session_updated_at) -> None:
        async def insert() -> None:
            async with SessionLocal() as db:
                db.add(
                    Message(
                        session_id=session_id,
                        role="user",
                        content="hello",
                        created_at=message_at,
                    )
                )
                session = await db.get(Session, session_id)
                assert session is not None
                session.updated_at = session_updated_at
                await db.commit()

        asyncio.run(insert())

    # A late run completion bumps `updated_at`, but the conversation itself is
    # older, so it must stay below the session with the newer message.
    seed(older, base, base + timedelta(hours=2))
    seed(newer, base + timedelta(minutes=5), base + timedelta(minutes=5))

    sessions = client.get(f"/api/agents/{agent_id}/sessions").json()
    by_id = {item["id"]: item for item in sessions}
    assert by_id[older]["updated_at"] > by_id[newer]["updated_at"]
    assert by_id[older]["last_message_at"] < by_id[newer]["last_message_at"]
    ids = [item["id"] for item in sessions]
    assert ids.index(newer) < ids.index(older)


def test_session_list_falls_back_to_created_at_without_messages(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    created = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()
    sessions = client.get(f"/api/agents/{agent_id}/sessions").json()
    item = next(entry for entry in sessions if entry["id"] == created["id"])
    assert item["last_message_at"] == item["created_at"]


def test_chat_queues_follow_up_runs(client) -> None:
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

    follow_up = client.post("/api/chat/messages", json={"session_id": session_id, "content": "again"})
    assert follow_up.status_code == 201
    follow_up_id = follow_up.json()["run_id"]

    follow_up_run = client.get(f"/api/runs/{follow_up_id}").json()
    assert follow_up_run["status"] == "queued"

    active = client.get(f"/api/sessions/{session_id}/active-run").json()
    assert active["id"] == run_id

    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [message["role"] for message in messages] == ["user", "user"]
    assert [message["run_id"] for message in messages] == [run_id, follow_up_id]


def test_chat_queues_follow_up_behind_running_run(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]

    first = client.post("/api/chat/messages", json={"session_id": session_id, "content": "one"})
    assert first.status_code == 201
    first_id = first.json()["run_id"]
    _set_run_running(first_id)

    second = client.post("/api/chat/messages", json={"session_id": session_id, "content": "two"})
    assert second.status_code == 201
    second_id = second.json()["run_id"]

    active = client.get(f"/api/sessions/{session_id}/active-run").json()
    assert active["id"] == first_id
    assert active["status"] == "running"
    assert client.get(f"/api/runs/{second_id}").json()["status"] == "queued"


def test_queued_follow_up_keeps_conversation_order(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]

    first = client.post("/api/chat/messages", json={"session_id": session_id, "content": "Q1"})
    second = client.post("/api/chat/messages", json={"session_id": session_id, "content": "Q2"})
    assert first.status_code == 201
    assert second.status_code == 201
    first_id = first.json()["run_id"]
    second_id = second.json()["run_id"]

    for run_id, output in ((first_id, "A1"), (second_id, "A2")):
        asyncio.run(
            complete_run(
                run_id,
                output=output,
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            )
        )

    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "Q1"),
        ("assistant", "A1"),
        ("user", "Q2"),
        ("assistant", "A2"),
    ]


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
    assert info["capabilities"]["filesystem"] is True
    assert info["capabilities"]["skills"] is True
    assert info["capabilities"]["chat"] is True
    assert info["capabilities"]["memory"] is True
    assert info["skills_root"]
    assert info["counts"]["providers"] >= 2
    assert info["counts"]["agents"] >= 1
    assert info["knowledge"]["enabled"] is False
    assert info["knowledge"]["local_dir"]


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
    message_id = response.json()["message_id"]

    memories = client.get("/api/memories?scope=user").json()
    item = next(item for item in memories if "以后回答先给结论" in item["content"])
    assert item["source_kind"] == "explicit"
    assert item["snapshot_text"] == "记住：以后回答先给结论"
    assert item["source_message_id"] == message_id
    assert item["source_session_id"] == session_id
    assert item["snapshot_at"] is not None
    assert item["source_message_exists"] is True
    assert item["source_session_exists"] is True

    events = client.get(f"/api/runs/{run_id}/events").json()
    assert any(
        event["type"] == "memory_saved"
        and event["payload"].get("source") == "explicit"
        for event in events
    )

    # Editing the text makes it a manual note; the old quote is no longer
    # evidence for the new wording.
    edited = client.put(
        f"/api/memories/{item['id']}",
        json={"content": "回答先给结论，再展开", "tags": ["style"]},
    )
    assert edited.status_code == 200
    assert edited.json()["source_kind"] == "manual"
    assert edited.json()["snapshot_text"] is None
    assert edited.json()["source_message_id"] is None


def test_explicit_memory_tags_edit_keeps_snapshot(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    client.post(
        "/api/chat/messages",
        json={"session_id": session_id, "content": "记住：回答保持简洁"},
    )
    item = next(
        item
        for item in client.get("/api/memories?scope=user").json()
        if "回答保持简洁" in item["content"]
    )

    updated = client.put(f"/api/memories/{item['id']}", json={"tags": ["style"]})
    assert updated.status_code == 200
    assert updated.json()["source_kind"] == "explicit"
    assert updated.json()["snapshot_text"] == "记住：回答保持简洁"


def test_manual_memory_has_no_snapshot(client) -> None:
    created = client.post(
        "/api/memories", json={"scope": "user", "content": "手动写入的偏好"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["source_kind"] == "manual"
    assert body["snapshot_text"] is None
    assert body["source_message_id"] is None


def test_manual_duplicate_does_not_overwrite_explicit_snapshot(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    client.post(
        "/api/chat/messages",
        json={"session_id": session_id, "content": "记住：重复内容不覆盖快照"},
    )

    duplicate = client.post(
        "/api/memories",
        json={"scope": "user", "content": "重复内容不覆盖快照"},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["source_kind"] == "explicit"
    assert duplicate.json()["snapshot_text"] == "记住：重复内容不覆盖快照"


def test_explicit_memory_survives_session_delete(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    response = client.post(
        "/api/chat/messages",
        json={"session_id": session_id, "content": "记住：会话删除后快照仍在"},
    )
    run_id = response.json()["run_id"]

    async def finish_run() -> None:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            assert run is not None
            run.status = "completed"
            run.finished_at = utcnow()
            await db.commit()

    asyncio.run(finish_run())
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200

    item = next(
        item
        for item in client.get("/api/memories?scope=user").json()
        if "会话删除后快照仍在" in item["content"]
    )
    assert item["snapshot_text"] == "记住：会话删除后快照仍在"
    assert item["source_message_exists"] is False
    assert item["source_session_exists"] is False


def test_agent_auto_memory_flag(client) -> None:
    created = client.post(
        "/api/agents", json={"name": "NoMemory", "auto_memory": False}
    ).json()
    assert created["auto_memory"] is False

    updated = client.put(
        f"/api/agents/{created['id']}", json={"auto_memory": True}
    ).json()
    assert updated["auto_memory"] is True
