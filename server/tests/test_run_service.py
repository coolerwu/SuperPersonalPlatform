import asyncio
import json

from server.app.run_service import RunService
from server.app.session_service import SessionService


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
      deepagent:
        max_iterations: 7
        todo_list: true
        filesystem:
          enabled: true
          root: agent
          mode: read_write
        use_longterm_memory: true
        tools:
          - search_context
"""


def test_run_service_persists_index_state_events_and_result(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    files_dir = tmp_path / "context" / "knowledge" / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "profile.md").write_text("local knowledge", encoding="utf-8")

    captured = {}

    async def fake_run(self, *, instructions, messages, options):
        captured["options"] = options
        captured["messages"] = messages
        return f"answer: {messages[-1].content}"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    run_id = run["run_id"]
    completed = asyncio.run(service.execute_run(run_id))

    assert completed["state"]["status"] == "completed"
    assert completed["result"]["content"] == "answer: hello"
    assert captured["options"].max_iterations == 7
    assert captured["options"].todo_list is True
    assert captured["options"].filesystem_enabled is True
    assert captured["options"].use_longterm_memory is True
    assert captured["options"].tools == ("search_context",)
    assert captured["messages"][-1].content == "hello"
    assert (tmp_path / "runs" / "index.json").exists()
    assert (tmp_path / "runs" / run_id / "input.json").exists()
    assert (tmp_path / "runs" / run_id / "state.json").exists()
    assert (tmp_path / "runs" / run_id / "events.jsonl").exists()
    assert (tmp_path / "runs" / run_id / "result.json").exists()
    assert (tmp_path / "runs" / run_id / "lock.json").exists()
    assert (tmp_path / "runs" / run_id / "delivery.json").exists()
    run_input = json.loads((tmp_path / "runs" / run_id / "input.json").read_text(encoding="utf-8"))
    assert run_input["snapshot"]["context"]["files"][0]["path"] == "/files/profile.md"

    index = json.loads((tmp_path / "runs" / "index.json").read_text(encoding="utf-8"))
    assert index["runs"][0]["run_id"] == run_id
    assert index["runs"][0]["status"] == "completed"
    assert service.get_events(run_id, after=0)[-1]["type"] == "completed"


def test_run_service_persists_session_history(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    session_service = SessionService(tmp_path)
    session = session_service.get_or_create(
        channel="wechat",
        channel_account_id="main",
        peer_type="private",
        peer_id="wxid_demo",
        agent_id="assistant",
    )

    captured = {}

    async def fake_run(self, *, instructions, messages, options):
        captured["messages"] = messages
        return "session answer"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path, session_service=session_service)
    run = asyncio.run(service.create_run(content="第二句", agent_id="assistant", source="wechat", session_id=session.session_id))
    run_id = run["run_id"]
    completed = asyncio.run(service.execute_run(run_id))

    assert completed["input"]["session_id"] == session.session_id
    assert completed["state"]["session_id"] == session.session_id
    assert completed["delivery"]["session_id"] == session.session_id
    assert captured["messages"][-1].role == "user"
    assert captured["messages"][-1].content == "第二句"

    messages_path = tmp_path / "sessions" / session.session_id / "messages.jsonl"
    messages = [json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines()]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == "session answer"

    session_index = json.loads((tmp_path / "sessions" / "index.json").read_text(encoding="utf-8"))
    assert session_index["sessions"][0]["session_id"] == session.session_id
    assert session_index["sessions"][0]["last_run_id"] == run_id

    run_index = json.loads((tmp_path / "runs" / "index.json").read_text(encoding="utf-8"))
    assert run_index["runs"][0]["session_id"] == session.session_id


def test_run_service_rejects_unknown_session_before_writing_run(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    service = RunService(tmp_path, session_service=SessionService(tmp_path))

    try:
        asyncio.run(service.create_run(content="hello", agent_id="assistant", session_id="missing"))
    except ValueError as exc:
        assert str(exc) == "session does not exist"
    else:
        raise AssertionError("expected unknown session to be rejected")

    assert not (tmp_path / "runs").exists()
