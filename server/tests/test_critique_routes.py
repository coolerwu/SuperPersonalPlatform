import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.auth_routes import create_auth_router
from server.adapter.critique_routes import create_critique_router
from server.adapter.dependencies import AppContainer
from server.app.agent_chat_service import AgentChatService
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.critique_service import CritiqueService
from server.domain.auth import AuthToken
from server.infrastructure.session import SessionCodec


def write_config(workspace) -> None:
    workspace.joinpath("config.yaml").write_text(
        """
auth:
  token: secret-token
proxy:
  upstream_base_url: http://example.test/
llm:
  default_model_id: fast
  models:
    - id: fast
      name: Fast
      base_url: https://example.test/v1
      api_key: secret
      model: test-model
      mode: prompt
agents:
  definitions: []
""".strip(),
        encoding="utf-8",
    )


def make_client(tmp_path) -> TestClient:
    write_config(tmp_path)
    config_service = ConfigFileService(tmp_path)
    agent_service = AgentChatService(config_service.config_path)
    critique_service = CritiqueService(tmp_path, agent_service)
    container = AppContainer(
        auth_service=AuthService(AuthToken("secret-token")),
        config_file_service=config_service,
        proxy_service=None,
        system_log_service=None,
        system_update_service=None,
        session_codec=SessionCodec("secret-token"),
        agent_chat_service=agent_service,
        critique_service=critique_service,
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_critique_router(container))
    return TestClient(app)


DISCIPLINE_PAYLOAD = {
    "name": "经济学",
    "known_scope": "微观决策与机会成本",
    "critique_focus": "成本、激励和替代方案",
    "default_enabled": True,
}


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"token": "secret-token"})
    assert response.status_code == 200


def test_critique_discipline_routes_require_auth_and_support_crud(tmp_path) -> None:
    client = make_client(tmp_path)

    assert client.get("/api/critique/disciplines").status_code == 401
    login(client)

    created_response = client.post(
        "/api/critique/disciplines", json=DISCIPLINE_PAYLOAD
    )
    assert created_response.status_code == 201
    created = created_response.json()["discipline"]
    assert created["name"] == "经济学"
    assert client.get("/api/critique/disciplines").json()["disciplines"] == [created]

    updated_response = client.put(
        f"/api/critique/disciplines/{created['id']}",
        json={**DISCIPLINE_PAYLOAD, "known_scope": "行为经济学", "default_enabled": False},
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["discipline"]["known_scope"] == "行为经济学"
    assert updated_response.json()["discipline"]["default_enabled"] is False

    assert client.delete(f"/api/critique/disciplines/{created['id']}").json() == {"ok": True}
    assert client.get("/api/critique/disciplines").json() == {"disciplines": []}


def test_critique_run_websocket_streams_progress_and_persists_history(
    tmp_path, monkeypatch
) -> None:
    async def fake_run_agent(request):
        if request.agent.definition.id == "critique-judge":
            return json.dumps(
                {
                    "weakest_assumption": "没有付费证据",
                    "largest_disagreement": "暂无",
                    "recommended_validation": "先验证十个付费用户",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "core_assumption": "需求真实存在",
                "counterevidence": "没有付费数据",
                "opportunity_cost": "放弃稳定收入",
                "key_question": "谁会持续付费？",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("server.app.critique_service.run_agent", fake_run_agent)
    client = make_client(tmp_path)
    login(client)
    discipline = client.post(
        "/api/critique/disciplines", json=DISCIPLINE_PAYLOAD
    ).json()["discipline"]

    with client.websocket_connect("/api/critique/runs/connect") as websocket:
        assert websocket.receive_json() == {"type": "status", "status": "connected"}
        websocket.send_json(
            {
                "type": "run",
                "question": "我是否应该辞职做自己的产品？",
                "discipline_ids": [discipline["id"]],
            }
        )
        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "run_completed":
                break

    assert events[0]["type"] == "run_started"
    assert [event["status"] for event in events if event["type"] == "discipline_status"] == [
        "running",
        "completed",
    ]
    assert [event["status"] for event in events if event["type"] == "judgment_status"] == [
        "running",
        "completed",
    ]
    completed = events[-1]["run"]
    assert completed["status"] == "completed"
    history = client.get("/api/critique/runs").json()["runs"]
    assert history[0]["id"] == completed["id"]
    assert client.get(f"/api/critique/runs/{completed['id']}").json()["run"] == completed


def test_critique_run_websocket_retries_failed_discipline(tmp_path, monkeypatch) -> None:
    attempts = 0

    async def fake_run_agent(request):
        nonlocal attempts
        if request.agent.definition.id == "critique-judge":
            return json.dumps(
                {
                    "weakest_assumption": "需求未经验证",
                    "largest_disagreement": "暂无",
                    "recommended_validation": "先做预售",
                },
                ensure_ascii=False,
            )
        attempts += 1
        if attempts == 1:
            return "invalid"
        return json.dumps(
            {
                "core_assumption": "用户会付费",
                "counterevidence": "没有订单",
                "opportunity_cost": "放弃工资",
                "key_question": "谁已付费？",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("server.app.critique_service.run_agent", fake_run_agent)
    client = make_client(tmp_path)
    login(client)
    discipline = client.post(
        "/api/critique/disciplines", json=DISCIPLINE_PAYLOAD
    ).json()["discipline"]

    with client.websocket_connect("/api/critique/runs/connect") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "run", "question": "是否辞职？", "discipline_ids": [discipline["id"]]}
        )
        while True:
            failed = websocket.receive_json()
            if failed["type"] == "run_completed":
                break
        assert failed["run"]["status"] == "failed"

        websocket.send_json(
            {
                "type": "retry",
                "run_id": failed["run"]["id"],
                "discipline_id": discipline["id"],
            }
        )
        retried = websocket.receive_json()
        assert retried["type"] != "error"
        while True:
            if retried["type"] == "run_completed":
                break
            retried = websocket.receive_json()

    assert retried["run"]["status"] == "completed"
    assert retried["run"]["results"][0]["analysis"]["key_question"] == "谁已付费？"
