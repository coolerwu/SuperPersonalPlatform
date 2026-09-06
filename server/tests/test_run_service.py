import asyncio
import json
from datetime import datetime, timedelta, timezone

from server.app.run_service import RunService
from server.app.session_service import SessionService
from server.domain.run_events import (
    DeepAgentGraphUpdatePayload,
    DeepAgentMessageDeltaPayload,
    DeepAgentSubagentResponsePayload,
    RunEventType,
)
from server.infrastructure.deepagent_runtime import DeepAgentStreamEvent


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


IMAGE_CONFIG = CONFIG.replace("model: gpt-4o-mini", "model: gpt-4o-mini\n      supports_images: true")


def test_run_service_persists_index_state_events_and_result(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    files_dir = tmp_path / "context" / "knowledge" / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "profile.md").write_text("local knowledge", encoding="utf-8")

    captured = {}

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
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
    assert not (tmp_path / "runs" / run_id / "lock.json").exists()
    assert (tmp_path / "runs" / run_id / "delivery.json").exists()
    run_input = json.loads((tmp_path / "runs" / run_id / "input.json").read_text(encoding="utf-8"))
    assert run_input["snapshot"]["context"]["files"][0]["path"] == "/files/profile.md"

    index = json.loads((tmp_path / "runs" / "index.json").read_text(encoding="utf-8"))
    assert index["runs"][0]["run_id"] == run_id
    assert index["runs"][0]["status"] == "completed"
    assert service.get_events(run_id, after=0)[-1]["type"] == "completed"


def test_run_service_reconciles_runs_interrupted_by_restart(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    run_id = run["run_id"]

    assert service.reconcile_incomplete_runs() == 1

    reconciled = service.get_run(run_id)
    assert reconciled["state"]["status"] == "failed"
    assert reconciled["state"]["error"]["type"] == "RunInterruptedError"
    assert reconciled["partial"]["status"] == "failed"
    assert reconciled["delivery"]["status"] == "failed"
    assert not (tmp_path / "runs" / run_id / "lock.json").exists()
    assert service.reconcile_incomplete_runs() == 0


def test_run_service_times_out_and_releases_lock(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
        await asyncio.sleep(1)
        return "late answer"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)
    monkeypatch.setattr("server.app.run_service.RUN_EXECUTION_TIMEOUT_SECONDS", 0.01)

    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    run_id = run["run_id"]

    try:
        asyncio.run(service.execute_run(run_id))
    except TimeoutError:
        pass
    else:
        raise AssertionError("run should time out")

    failed = service.get_run(run_id)
    assert failed["state"]["status"] == "failed"
    assert failed["state"]["error"] == {
        "message": "run exceeded the 0.01-second execution limit",
        "type": "RunExecutionTimeoutError",
    }
    assert failed["partial"]["status"] == "failed"
    assert not (tmp_path / "runs" / run_id / "lock.json").exists()


def test_run_service_reconciles_timed_out_run_when_reading_list(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    run_id = run["run_id"]
    stale_at = (datetime.now(timezone.utc) - timedelta(seconds=31 * 60)).isoformat()
    run_dir = tmp_path / "runs" / run_id
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state["status"] = "running"
    state["created_at"] = stale_at
    state["updated_at"] = stale_at
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    runs = service.list_runs()

    assert runs[0]["status"] == "failed"
    failed = service.get_run(run_id)
    assert failed["state"]["error"] == {
        "message": "run exceeded the 1800-second execution limit",
        "type": "RunExecutionTimeoutError",
    }
    assert failed["partial"]["status"] == "failed"
    assert not (run_dir / "lock.json").exists()


def test_run_service_reconciles_stale_heartbeat_when_reading_run(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    run_id = run["run_id"]
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=121)).isoformat()
    run_dir = tmp_path / "runs" / run_id
    lock = json.loads((run_dir / "lock.json").read_text(encoding="utf-8"))
    lock["heartbeat_at"] = stale_heartbeat
    (run_dir / "lock.json").write_text(json.dumps(lock), encoding="utf-8")

    failed = service.get_run(run_id)

    assert failed["state"]["status"] == "failed"
    assert failed["state"]["error"] == {
        "message": "run heartbeat is older than 120 seconds",
        "type": "RunStaleHeartbeatError",
    }
    assert not (run_dir / "lock.json").exists()


def test_run_service_marks_cancelled_execution_failed(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
        raise asyncio.CancelledError

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    run_id = run["run_id"]

    try:
        asyncio.run(service.execute_run(run_id))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("run should propagate cancellation")

    failed = service.get_run(run_id)
    assert failed["state"]["status"] == "failed"
    assert failed["state"]["error"] == {
        "message": "run execution task was cancelled before completion",
        "type": "RunExecutionCancelledError",
    }
    assert not (tmp_path / "runs" / run_id / "lock.json").exists()


def test_run_service_persists_streaming_partial_output(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
        assert stream_callback is not None
        stream_callback(DeepAgentStreamEvent(RunEventType.ASSISTANT_DELTA, DeepAgentMessageDeltaPayload(delta="正在")))
        stream_callback(DeepAgentStreamEvent(RunEventType.ASSISTANT_DELTA, DeepAgentMessageDeltaPayload(delta="回答")))
        return "最终回答"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    completed = asyncio.run(service.execute_run(run["run_id"]))

    assert completed["result"]["content"] == "最终回答"
    assert completed["partial"]["status"] == "completed"
    assert completed["partial"]["content"] == "最终回答"
    events = service.get_events(run["run_id"], after=0)
    delta_events = [event for event in events if event["type"] == "assistant_delta"]
    assert delta_events
    assert "".join(event["payload"]["delta"] for event in delta_events) == "正在回答"
    assert delta_events[0]["payload"]["kind"] == "deepagent_message_delta"


def test_run_service_persists_streaming_thinking_snapshot(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
        assert stream_callback is not None
        stream_callback(
            DeepAgentStreamEvent(
                RunEventType.AGENT_UPDATE,
                DeepAgentGraphUpdatePayload(nodes=("agent",), preview="正在搜索资料"),
            )
        )
        stream_callback(
            DeepAgentStreamEvent(
                RunEventType.SUBAGENT_RESPONSE,
                DeepAgentSubagentResponsePayload(
                    content="已完成资料核对",
                    namespace=("tools:task-123",),
                    agent="researcher",
                    node="model",
                    source_class="AIMessage",
                ),
            )
        )
        stream_callback(DeepAgentStreamEvent(RunEventType.ASSISTANT_DELTA, DeepAgentMessageDeltaPayload(delta="正文")))
        return "最终正文"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path)
    run = asyncio.run(service.create_run(content="hello", agent_id="assistant"))
    completed = asyncio.run(service.execute_run(run["run_id"]))

    assert completed["partial"]["status"] == "completed"
    assert completed["partial"]["content"] == "最终正文"
    assert completed["partial"]["thinking"] == [
        "DeepAgent started",
        "正在搜索资料",
        "子 Agent researcher：已完成资料核对",
    ]
    assert completed["partial"]["thinking_status"] == "completed"
    assert completed["partial"]["thinking_collapsed"] is True
    subagent_events = [event for event in service.get_events(run["run_id"], after=0) if event["type"] == "subagent_response"]
    assert subagent_events[0]["payload"] == {
        "agent": "researcher",
        "content": "已完成资料核对",
        "kind": "deepagent_subagent_response",
        "namespace": ("tools:task-123",),
        "node": "model",
        "source_class": "AIMessage",
    }


def test_run_service_finds_latest_active_schedule_run(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    service = RunService(tmp_path)
    first = asyncio.run(
        service.create_run(
            content="first",
            agent_id="assistant",
            source="schedule",
            metadata={"schedule_id": "daily"},
        )
    )
    asyncio.run(
        service.create_run(
            content="other",
            agent_id="assistant",
            source="schedule",
            metadata={"schedule_id": "other"},
        )
    )
    latest = asyncio.run(
        service.create_run(
            content="latest",
            agent_id="assistant",
            source="schedule",
            metadata={"schedule_id": "daily"},
        )
    )

    assert service.latest_active_run_for_schedule("daily") == latest["run_id"]

    service.fail_run(latest["run_id"], error={"type": "ScheduleStaleLockError", "message": "stale"})

    assert service.latest_active_run_for_schedule("daily") == first["run_id"]


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

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
        captured["messages"] = messages
        captured["checkpoint_path"] = checkpoint_path
        captured["thread_id"] = thread_id
        return "session answer"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path, session_service=session_service)
    run = asyncio.run(service.create_run(content="第二句", agent_id="assistant", source="wechat", session_id=session.session_id))
    run_id = run["run_id"]
    completed = asyncio.run(service.execute_run(run_id))

    assert completed["input"]["session_id"] == session.session_id
    assert completed["state"]["session_id"] == session.session_id
    assert completed["delivery"]["session_id"] == session.session_id
    assert captured["checkpoint_path"] == tmp_path / "sessions" / "checkpoints.sqlite"
    assert captured["thread_id"] == session.session_id
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


def test_run_service_uses_checkpoint_without_injecting_prior_session_context(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    session_service = SessionService(tmp_path)
    session = session_service.get_or_create(
        channel="wechat",
        channel_account_id="main",
        peer_type="private",
        peer_id="wxid_demo",
        agent_id="assistant",
    )
    session_service.append_message(
        session.session_id,
        role="user",
        content="以后推荐项目时，每个项目控制在150-200字，并且重点写优点和缺点。",
    )
    session_service.append_message(
        session.session_id,
        role="assistant",
        content="收到。",
    )

    captured = {}

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
        captured["instructions"] = instructions
        captured["messages"] = messages
        captured["thread_id"] = thread_id
        return "session answer"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path, session_service=session_service)
    run = asyncio.run(
        service.create_run(
            content="再给我几个类似项目",
            agent_id="assistant",
            source="wechat",
            session_id=session.session_id,
        )
    )
    asyncio.run(service.execute_run(run["run_id"]))

    assert "## Recent Session Context" not in captured["instructions"]
    assert "每个项目控制在150-200字" not in captured["instructions"]
    assert "再给我几个类似项目" not in captured["instructions"]
    assert len(captured["messages"]) == 1
    assert captured["messages"][-1].content == "再给我几个类似项目"
    assert captured["thread_id"] == session.session_id


def test_run_service_persists_session_image_attachments(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(IMAGE_CONFIG, encoding="utf-8")
    session_service = SessionService(tmp_path)
    session = session_service.get_or_create(
        channel="wechat",
        channel_account_id="main",
        peer_type="private",
        peer_id="wxid_image",
        agent_id="assistant",
    )

    captured = {}

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
        captured["messages"] = messages
        return "image answer"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path, session_service=session_service)
    run = asyncio.run(
        service.create_run(
            content="看看这张图",
            agent_id="assistant",
            source="wechat",
            session_id=session.session_id,
            attachments=(
                {
                    "id": "photo",
                    "type": "image",
                    "mime": "image/png",
                    "filename": "photo.png",
                    "bytes": b"\x89PNG\r\n\x1a\n",
                },
            ),
        )
    )
    completed = asyncio.run(service.execute_run(run["run_id"]))

    assert completed["result"]["content"] == "image answer"
    message = captured["messages"][-1]
    assert message.content == "看看这张图"
    assert message.attachments[0].mime == "image/png"
    assert message.attachments[0].path.read_bytes() == b"\x89PNG\r\n\x1a\n"

    run_input = json.loads((tmp_path / "runs" / run["run_id"] / "input.json").read_text(encoding="utf-8"))
    assert run_input["attachments"][0]["workspace_path"].startswith(f"sessions/{session.session_id}/attachments/")
    messages_path = tmp_path / "sessions" / session.session_id / "messages.jsonl"
    messages = [json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines()]
    assert messages[0]["attachments"][0]["filename"] == "photo.png"


def test_run_service_textifies_images_when_model_does_not_support_images(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    session_service = SessionService(tmp_path)
    session = session_service.get_or_create(
        channel="wechat",
        channel_account_id="main",
        peer_type="private",
        peer_id="wxid_image",
        agent_id="assistant",
    )

    captured = {}

    async def fake_run(self, *, instructions, messages, options, checkpoint_path=None, thread_id="", stream_callback=None):
        captured["messages"] = messages
        return "text-only answer"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    service = RunService(tmp_path, session_service=session_service)
    run = asyncio.run(
        service.create_run(
            content="",
            agent_id="assistant",
            source="wechat",
            session_id=session.session_id,
            attachments=(
                {
                    "id": "photo",
                    "type": "image",
                    "mime": "image/png",
                    "filename": "photo.png",
                    "bytes": b"\x89PNG\r\n\x1a\n",
                },
            ),
        )
    )
    completed = asyncio.run(service.execute_run(run["run_id"]))

    assert completed["state"]["status"] == "completed"
    assert completed["result"]["content"] == "text-only answer"
    message = captured["messages"][-1]
    assert message.attachments == ()
    assert "当前 Agent 主模型未开启图片能力" in message.content
    assert "系统没有读取图片画面内容" in message.content
    assert "filename=photo.png" in message.content
    assert "mime=image/png" in message.content
    assert f"workspace_path=sessions/{session.session_id}/attachments/" in message.content
    event_types = [event["type"] for event in service.get_events(run["run_id"], after=0)]
    assert "image_attachments_textified" in event_types


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
