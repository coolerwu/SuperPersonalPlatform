from pathlib import Path

from fastapi.testclient import TestClient

from server.infrastructure.config import parse_settings
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
"""


class FakeDeepAgentRuntime:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, **kwargs) -> str:
        return "scheduled result"


def test_schedule_routes_create_and_run_agent_schedule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("server.app.run_service.DeepAgentRuntime", FakeDeepAgentRuntime)
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    client = TestClient(create_app(parse_settings(_raw_config()), workspace=tmp_path))
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post(
        "/api/schedules",
        json={
            "id": "daily_review",
            "name": "Daily Review",
            "enabled": True,
            "trigger": {"kind": "interval", "seconds": 3600},
            "agent_id": "assistant",
            "prompt": "总结最近笔记",
        },
    )

    assert response.status_code == 200
    assert response.json()["definition"]["id"] == "daily_review"

    list_response = client.get("/api/schedules")

    assert list_response.status_code == 200
    assert any(item["definition"]["id"] == "daily_review" for item in list_response.json()["schedules"])

    run_response = client.post("/api/schedules/daily_review/run-now")

    assert run_response.status_code == 200
    assert run_response.json()["state"]["last_run_id"].startswith("run_")

    delete_response = client.delete("/api/schedules/daily_review")

    assert delete_response.status_code == 200


def _raw_config() -> dict:
    import yaml

    return yaml.safe_load(CONFIG)
