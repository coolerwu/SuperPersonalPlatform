import asyncio
import json

from server.app.run_service import RunService


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
      context_ids:
        - nutstore
"""


def test_run_service_persists_index_state_events_and_result(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    context_dir = tmp_path / "contexts" / "nutstore"
    (context_dir / "knowledge").mkdir(parents=True)
    (context_dir / "context.json").write_text('{"id":"nutstore","tools":["webdav"]}', encoding="utf-8")
    (context_dir / "knowledge" / "index.json").write_text('{"items":[]}', encoding="utf-8")

    async def fake_run(self, system_prompt, user_message, *, max_iterations=60):
        return f"answer: {user_message}"

    monkeypatch.setattr("server.infrastructure.model_runner.ModelRunner.run_deep_agent", fake_run)

    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    run_id = run["run_id"]
    completed = asyncio.run(service.execute_run(run_id))

    assert completed["state"]["status"] == "completed"
    assert completed["result"]["content"] == "answer: hello"
    assert (tmp_path / "runs" / "index.json").exists()
    assert (tmp_path / "runs" / run_id / "input.json").exists()
    assert (tmp_path / "runs" / run_id / "state.json").exists()
    assert (tmp_path / "runs" / run_id / "events.jsonl").exists()
    assert (tmp_path / "runs" / run_id / "result.json").exists()
    assert (tmp_path / "runs" / run_id / "lock.json").exists()
    assert (tmp_path / "runs" / run_id / "delivery.json").exists()

    index = json.loads((tmp_path / "runs" / "index.json").read_text(encoding="utf-8"))
    assert index["runs"][0]["run_id"] == run_id
    assert index["runs"][0]["status"] == "completed"
    assert service.get_events(run_id, after=0)[-1]["type"] == "completed"
