from fastapi.testclient import TestClient

from server.infrastructure.fastapi_app import create_app


CONFIG = """\
auth:
  token: secret-token
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://api.openai.com/v1
      api_key: test-key
      model: gpt-4o-mini
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.
      model_id: default
      context_ids: []
      deepagent:
        max_iterations: 7
"""


def test_chat_routes_create_web_session_and_run(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    async def fake_execute_background(container, run_id: str) -> None:
        container.system_log_service.append_line(f"fake chat run {run_id}")

    monkeypatch.setattr("server.adapter.chat_routes._execute_background", fake_execute_background)
    client = TestClient(create_app(workspace=tmp_path))

    assert client.post("/api/auth/login", json={"token": "secret-token"}).status_code == 200

    session_response = client.post("/api/chat/session", json={"agent_id": "assistant"})
    assert session_response.status_code == 200
    session_id = session_response.json()["session"]["session_id"]

    message_response = client.post(
        "/api/chat/messages",
        json={"agent_id": "assistant", "session_id": session_id, "content": "页面问答"},
    )
    assert message_response.status_code == 200
    payload = message_response.json()
    assert payload["session"]["session_id"] == session_id
    assert payload["run"]["input"]["source"] == "web_chat"
    assert payload["run"]["input"]["session_id"] == session_id

    restored_response = client.post("/api/chat/session", json={"agent_id": "assistant"})
    assert restored_response.status_code == 200
    active_run = restored_response.json()["active_run"]
    assert active_run["run_id"] == payload["run"]["run_id"]
    assert active_run["state"]["status"] == "queued"

    messages_response = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert messages_response.status_code == 200
    messages = messages_response.json()["messages"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "页面问答"


def test_chat_routes_list_and_change_web_sessions(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    async def fake_execute_background(container, run_id: str) -> None:
        container.system_log_service.append_line(f"fake chat run {run_id}")

    monkeypatch.setattr("server.adapter.chat_routes._execute_background", fake_execute_background)
    client = TestClient(create_app(workspace=tmp_path))

    assert client.post("/api/auth/login", json={"token": "secret-token"}).status_code == 200

    first_response = client.post("/api/chat/session", json={"agent_id": "assistant"})
    assert first_response.status_code == 200
    first_session_id = first_response.json()["session"]["session_id"]
    message_response = client.post(
        "/api/chat/messages",
        json={"agent_id": "assistant", "session_id": first_session_id, "content": "旧会话内容"},
    )
    assert message_response.status_code == 200

    new_response = client.post("/api/chat/session/new", json={"agent_id": "assistant"})
    assert new_response.status_code == 200
    second_session_id = new_response.json()["session"]["session_id"]
    assert second_session_id != first_session_id

    list_response = client.get("/api/chat/sessions?agent_id=assistant")
    assert list_response.status_code == 200
    sessions = list_response.json()["sessions"]
    session_ids = [item["session_id"] for item in sessions]
    assert first_session_id in session_ids
    assert second_session_id in session_ids
    assert next(item for item in sessions if item["session_id"] == second_session_id)["active"] is True

    change_response = client.post(
        "/api/chat/session/change",
        json={"agent_id": "assistant", "selector": first_session_id},
    )
    assert change_response.status_code == 200
    payload = change_response.json()
    assert payload["session"]["session_id"] == first_session_id
    assert payload["session"]["active"] is True
    assert payload["messages"][-1]["content"] == "旧会话内容"
