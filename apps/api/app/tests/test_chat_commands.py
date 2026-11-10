from typing import Any

from fastapi.testclient import TestClient


class FakeWorker:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return True


def _agent_id(client: TestClient) -> str:
    return client.get("/api/agents").json()[0]["id"]


def _create_session(client: TestClient, agent_id: str) -> str:
    response = client.post(f"/api/agents/{agent_id}/sessions", json={})
    assert response.status_code == 201
    return response.json()["id"]


def _command(
    client: TestClient,
    agent_id: str,
    session_id: str | None,
    command: str,
) -> Any:
    return client.post(
        "/api/chat/commands",
        json={"agent_id": agent_id, "session_id": session_id, "command": command},
    )


def _send(client: TestClient, session_id: str, content: str) -> str:
    response = client.post(
        "/api/chat/messages", json={"session_id": session_id, "content": content}
    )
    assert response.status_code == 201
    return response.json()["run_id"]


def test_help_and_unknown_commands_create_no_run_or_message(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)

    help_result = _command(client, agent_id, session_id, "/help")
    assert help_result.status_code == 200
    assert "/new" in help_result.json()["reply"]
    assert help_result.json()["cancelled_count"] == 0

    unknown = _command(client, agent_id, session_id, "/foo")
    assert unknown.status_code == 200
    assert "未知命令" in unknown.json()["reply"]

    assert client.get(f"/api/sessions/{session_id}/messages").json() == []
    assert client.get(f"/api/sessions/{session_id}/active-run").json() is None


def test_status_reports_agent_session_and_run_counts(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)
    _send(client, session_id, "first")
    _send(client, session_id, "second")

    result = _command(client, agent_id, session_id, "/status")
    assert result.status_code == 200
    reply = result.json()["reply"]
    assert reply.startswith("Agent：")
    assert "会话：" in reply
    assert "运行中：0 个，排队：2 个" in reply


def test_status_without_session_explains_how_to_start(client) -> None:
    agent_id = _agent_id(client)
    result = _command(client, agent_id, None, "/status")
    assert result.status_code == 200
    assert "还没有会话" in result.json()["reply"]


def test_new_mentions_active_runs_and_does_not_create_a_session(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)
    _send(client, session_id, "hello")
    before = client.get(f"/api/agents/{agent_id}/sessions").json()

    result = _command(client, agent_id, session_id, "/new")
    assert result.status_code == 200
    assert result.json()["command"] == "/new"
    assert "任务" in result.json()["reply"]

    after = client.get(f"/api/agents/{agent_id}/sessions").json()
    assert [item["id"] for item in after] == [item["id"] for item in before]


def test_stop_cancels_every_active_run_in_the_session(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)
    first = _send(client, session_id, "first")
    second = _send(client, session_id, "second")

    worker = FakeWorker()
    client.app.state.worker = worker
    result = _command(client, agent_id, session_id, "/stop")
    assert result.status_code == 200
    assert result.json()["cancelled_count"] == 2
    assert "已停止 2 个任务" in result.json()["reply"]
    assert set(worker.cancelled) == {first, second}


def test_stop_without_active_runs_is_a_noop(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)
    client.app.state.worker = FakeWorker()

    result = _command(client, agent_id, session_id, "/stop")
    assert result.status_code == 200
    assert "没有" in result.json()["reply"]
    assert result.json()["cancelled_count"] == 0


def test_stop_reports_unavailable_without_a_worker(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)
    _send(client, session_id, "hello")

    result = _command(client, agent_id, session_id, "/stop")
    assert result.status_code == 200
    assert "不可用" in result.json()["reply"]


def test_command_rejects_non_command_text(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)

    result = _command(client, agent_id, session_id, "hello")
    assert result.status_code == 400


def test_memory_command_writes_user_memory(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)
    command = "/memory 回答先给结论"

    result = _command(client, agent_id, session_id, command)
    assert result.status_code == 200
    assert result.json()["command"] == "/memory"
    assert "已写入用户记忆" in result.json()["reply"]

    item = next(
        item
        for item in client.get("/api/memories?scope=user").json()
        if item["content"] == "回答先给结论"
    )
    assert item["source_kind"] == "command"
    assert item["snapshot_text"] == command
    assert item["snapshot_at"] is not None
    assert client.get(f"/api/sessions/{session_id}/messages").json() == []


def test_memory_command_requires_content_and_accepts_long_content(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)

    usage = _command(client, agent_id, session_id, "/memory")
    assert usage.status_code == 200
    assert "用法" in usage.json()["reply"]

    content = "字" * 500
    saved = _command(client, agent_id, session_id, f"/memory {content}")
    assert saved.status_code == 200
    assert "已写入用户记忆" in saved.json()["reply"]
    item = next(
        item
        for item in client.get("/api/memories?scope=user").json()
        if item["content"] == content
    )
    assert item["source_kind"] == "command"


def test_memory_command_is_idempotent(client) -> None:
    agent_id = _agent_id(client)
    session_id = _create_session(client, agent_id)

    first = _command(client, agent_id, session_id, "/memory 同一内容只存一次")
    second = _command(client, agent_id, session_id, "/memory 同一内容只存一次")
    assert first.status_code == 200
    assert second.status_code == 200
    items = [
        item
        for item in client.get("/api/memories?scope=user").json()
        if item["content"] == "同一内容只存一次"
    ]
    assert len(items) == 1


def test_command_rejects_session_from_another_agent(client) -> None:
    first_agent = _agent_id(client)
    second = client.post("/api/agents", json={"name": "Second"})
    assert second.status_code == 201
    second_agent = second.json()["id"]
    second_session = _create_session(client, second_agent)

    result = _command(client, first_agent, second_session, "/status")
    assert result.status_code == 404
