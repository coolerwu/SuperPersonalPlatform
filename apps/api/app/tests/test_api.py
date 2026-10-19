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
    assert info["counts"]["providers"] >= 2
    assert info["counts"]["agents"] >= 1


def test_provider_create_rejects_blank_fields(client) -> None:
    response = client.post(
        "/api/providers",
        json={"name": "   ", "base_url": " ", "model": " "},
    )
    assert response.status_code == 400
