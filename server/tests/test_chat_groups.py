import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.chat_group_service import ChatGroupService, GroupConflict
from server.app.session_service import SessionService
from server.domain.chat_group import GroupDefinition
from server.infrastructure.fastapi_app import create_app, create_container
from server.infrastructure.config import load_settings
from server.infrastructure.group_control import group_control_tool
from server.tests.test_chat_routes import CONFIG


def setup(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG)
    container = create_container(load_settings(tmp_path / "config.yaml"), tmp_path)
    return container, container.chat_group_service


def definition():
    return GroupDefinition(name="设计组", host_member_id="host", members=[
        {"id": "host", "agent_id": "assistant", "name": "主持", "prompt": "负责组织"},
        {"id": "review", "agent_id": "assistant", "name": "评审", "prompt": "检查问题"},
    ])


def fake_runtime(monkeypatch, callback=None):
    from server.app import run_service
    calls = []

    async def run(self, **kwargs):
        calls.append(kwargs)
        if callback:
            callback(self, kwargs)
        return f"答复 {len(calls)}"

    monkeypatch.setattr(run_service.DeepAgentRuntime, "run", run)
    return calls


async def execute_current(container, service, group_id):
    await service.tick()
    group = service.detail(group_id)
    step = group["executions"][-1]["current_step"]
    if step:
        await container.run_service.execute_run(step["run_id"])
        await service.tick()
    return service.detail(group_id)


def test_manual_chain_context_isolation_dedup_and_restart(tmp_path, monkeypatch):
    async def scenario():
        container, service = setup(tmp_path)
        calls = fake_runtime(monkeypatch)
        group = await service.create(definition())
        gid = group["id"]
        await service.send(gid, "请设计", ["host", "review", "host"], "first")
        await service.send(gid, "请设计", ["host", "review"], "first")
        await service.tick()
        first = service.detail(gid)["active_run"]["run_id"]
        # Simulate restart after run creation before completion.
        service = ChatGroupService(tmp_path, container.run_service, container.run_worker_service)
        await service.tick()
        assert service.detail(gid)["active_run"]["run_id"] == first
        await container.run_service.execute_run(first)
        await service.tick()
        await service.tick()  # Create the next member run.
        next_run = service.detail(gid)["active_run"]
        assert "答复 1" in next_run["input"]["content"]
        await container.run_service.execute_run(next_run["run_id"])
        await service.tick()
        done = service.read(gid)
        assert done["executions"][-1]["status"] == "completed"
        assert len(done["messages"]) == 3
        assert len(container.run_service.list_runs()) == 2
        assert calls[0]["thread_id"] != calls[1]["thread_id"]
        assert calls[0]["messages"][-1].id == f"group-input-{first}"
        assert "负责组织" in calls[0]["instructions"] and "检查问题" in calls[1]["instructions"]
        assert service.sessions.summaries_for_agent(agent_id="assistant") == []
        await service.send(gid, "继续评审", ["review"], "second")
        await service.tick()
        assert "请设计" not in service.detail(gid)["active_run"]["input"]["content"]
        second_group = await service.create(definition())
        await service.send(second_group["id"], "另一个群", ["host"], "first")
        await service.tick()
        assert service.detail(second_group["id"])["active_run"]["input"]["session_id"] not in done["member_sessions"].values()
    asyncio.run(scenario())


def test_host_three_rounds_and_continue(tmp_path, monkeypatch):
    def decision(runtime, kwargs):
        control = kwargs["options"].group_control
        if control:
            tool = group_control_tool(tmp_path / "runs" / control["run_id"] / "group_decision.json", control["control_members"], control["finish_only"])
            tool.invoke({"action": "finish", "summary": "已完成三轮，未完成项待继续"} if control["finish_only"] else
                        {"action": "dispatch", "tasks": [{"member_id": "review", "task": "检查"}]})

    async def scenario():
        container, service = setup(tmp_path)
        calls = fake_runtime(monkeypatch, decision)
        group = await service.create(definition())
        await service.send(group["id"], "自动完成设计", [], "auto", automatic=True)
        for _ in range(7):
            group = await execute_current(container, service, group["id"])
        execution = group["executions"][-1]
        assert execution["round"] == 3 and execution["status"] == "completed"
        assert len(calls) == 7
        assert group["messages"][-1]["content"] == "已完成三轮，未完成项待继续"
        await service.action(group["id"], execution["id"], "continue", "continue-id")
        await service.action(group["id"], execution["id"], "continue", "continue-id")
        assert len(service.read(group["id"])["executions"]) == 2
    asyncio.run(scenario())


def test_pause_retry_stop_and_config_lock(tmp_path, monkeypatch):
    async def scenario():
        container, service = setup(tmp_path)
        fake_runtime(monkeypatch)
        group = await service.create(definition())
        await service.send(group["id"], "工作", ["review", "host"], "manual")
        with pytest.raises(GroupConflict):
            await service.update(group["id"], definition())
        with pytest.raises(GroupConflict):
            await service.send(group["id"], "其它", [], "other")
        await service.tick()
        group = service.detail(group["id"])
        run_id = group["active_run"]["run_id"]
        container.run_service._set_state(run_id, "waiting_approval")
        await service.tick()
        assert service.read(group["id"])["executions"][-1]["status"] == "waiting_approval"
        assert len(container.run_service.list_runs()) == 1
        container.run_service._set_state(run_id, "failed")
        await service.tick()
        assert service.read(group["id"])["executions"][-1]["status"] == "paused"
        eid = group["executions"][-1]["id"]
        await service.action(group["id"], eid, "retry")
        assert container.run_service.get_run(run_id)["state"]["status"] == "queued"
        await service.action(group["id"], eid, "stop")
        await service.tick()
        assert service.read(group["id"])["executions"][-1]["status"] == "cancelled"
        assert container.run_service.get_run(run_id)["state"]["status"] == "cancelled"
        assert len(container.run_service.list_runs()) == 1
    asyncio.run(scenario())


def test_structured_control_validation_and_idempotency(tmp_path):
    tool = group_control_tool(tmp_path / "decision.json", ["review"], False)
    for invalid in [
        {"action": "dispatch", "tasks": []},
        {"action": "dispatch", "tasks": [{"member_id": "unknown", "task": "x"}]},
        {"action": "dispatch", "tasks": [{"member_id": "review", "task": "x"}] * 5},
        {"action": "finish", "summary": ""},
    ]:
        with pytest.raises(ValueError):
            tool.invoke(invalid)
    payload = {"action": "finish", "summary": "完成"}
    tool.invoke(payload)
    tool.invoke(payload)
    with pytest.raises(ValueError):
        tool.invoke({"action": "finish", "summary": "另一个决定"})
    with pytest.raises(ValueError):
        group_control_tool(tmp_path / "other.json", ["review"], True).invoke({"action": "dispatch", "tasks": [{"member_id": "review", "task": "x"}]})


def test_missing_host_decision_pauses_without_publishing(tmp_path, monkeypatch):
    async def scenario():
        container, service = setup(tmp_path)
        fake_runtime(monkeypatch)
        group = await service.create(definition())
        await service.send(group["id"], "请自动分工", [], "auto", automatic=True)
        group = await execute_current(container, service, group["id"])
        assert group["executions"][-1]["status"] == "paused"
        assert len(group["messages"]) == 1
    asyncio.run(scenario())


def test_routes_auth_validation_archiving_and_internal_sessions(tmp_path):
    container, service = setup(tmp_path)
    client = TestClient(create_app(workspace=tmp_path))
    assert client.get("/api/chat-groups").status_code == 401
    client.post("/api/auth/login", json={"token": "secret-token"})
    assert client.post("/api/chat-groups", json={}).status_code == 422
    group = client.post("/api/chat-groups", json=definition().model_dump()).json()
    path = f'/api/chat-groups/{group["id"]}'
    request = {"content": "点名", "mentions": ["missing"], "client_message_id": "1"}
    assert client.post(path + "/messages", json=request).status_code == 400
    request["mentions"] = ["host"]
    assert client.post(path + "/messages", json=request).status_code == 200
    assert client.post(path + "/messages", json=request).status_code == 200
    assert len(client.get(path + "/messages").json()["messages"]) == 1
    assert client.get(path + "/messages?after=1").json()["messages"] == []
    asyncio.run(service.tick())
    sid = service.detail(group["id"])["active_run"]["input"]["session_id"]
    assert client.get(f"/api/chat/sessions/{sid}/messages?agent_id=assistant").status_code == 404
    assert client.post("/api/chat/session/change", json={"agent_id": "assistant", "selector": sid}).status_code == 404
    assert client.post("/api/runs", json={"agent_id": "assistant", "session_id": sid, "content": "绕过群"}).status_code == 400
    assert client.get("/api/chat-groups/not-a-group").status_code == 404


def test_group_sessions_survive_maintenance_without_bindings(tmp_path, monkeypatch):
    async def scenario():
        container, service = setup(tmp_path)
        fake_runtime(monkeypatch)
        group = await service.create(definition())
        await service.send(group["id"], "消息", [], "1")
        group = await execute_current(container, service, group["id"])
        (tmp_path / "sessions" / "active.json").write_text('{"bindings": []}')
        future = datetime.now(timezone.utc) + timedelta(days=40)
        ids = container.maintenance_service._protected_session_ids(future)
        assert set(group["member_sessions"].values()) <= ids
    asyncio.run(scenario())


def test_partial_run_creation_recovered_without_duplicate_history(tmp_path, monkeypatch):
    async def scenario():
        container, service = setup(tmp_path)
        group = await service.create(definition())
        await service.send(group["id"], "消息", [], "1")
        await service.tick()
        run = service.detail(group["id"])["active_run"]
        run_dir = tmp_path / "runs" / run["run_id"]
        (run_dir / "state.json").unlink()
        (tmp_path / "runs" / "index.json").write_text('{"runs": []}')
        await service.tick()
        assert service.detail(group["id"])["active_run"]["state"]["status"] == "queued"
        history = SessionService(tmp_path).read_messages(run["input"]["session_id"])
        assert len(history) == 1
        assert len(container.run_service.list_runs()) == 1
    asyncio.run(scenario())


def test_real_runtime_control_is_host_only_and_message_ids_reach_langgraph(tmp_path, monkeypatch):
    import deepagents
    from server.domain.agent_config import ModelDefinition
    from server.infrastructure.deepagent_runtime import DeepAgentRuntime, DeepAgentRuntimeOptions, RuntimeMessage
    captured = {}

    class FakeAgent:
        async def ainvoke(self, state, config):
            captured["state"] = state
            return {"messages": [type("Message", (), {"content": "ok"})()]}

    def create(**kwargs):
        captured.update(kwargs)
        return FakeAgent()

    monkeypatch.setattr(deepagents, "create_deep_agent", create)
    runtime = DeepAgentRuntime(ModelDefinition(id="default", name="default", base_url="https://example.com", api_key="test", model="test"),
        context_workspace=tmp_path / "context", agent_id="assistant", agent_workspace=tmp_path / "agents" / "assistant" / "workspace")
    asyncio.run(runtime.run(instructions="主持", messages=(RuntimeMessage(role="user", content="任务", id="stable-input"),),
        options=DeepAgentRuntimeOptions(group_control={"run_id": "run_test", "control_members": ["host"], "finish_only": False})))
    assert "group_decision" in [tool.name for tool in captured["tools"]]
    assert "group_decision" not in [tool.name for tool in captured["subagents"][0]["tools"]]
    assert captured["state"]["messages"][0].id == "stable-input"


def test_config_snapshot_and_removed_agent_fail_explicitly(tmp_path, monkeypatch):
    async def scenario():
        container, service = setup(tmp_path)
        calls = fake_runtime(monkeypatch)
        group = await service.create(definition())
        await service.send(group["id"], "工作", [], "1")
        (tmp_path / "config.yaml").write_text(CONFIG.replace("Be direct.", "Changed.").replace("gpt-4o-mini", "other-model"))
        await execute_current(container, service, group["id"])
        assert "Be direct." in calls[0]["instructions"]
        assert "Changed." not in calls[0]["instructions"]
        run = container.run_service.list_runs()[0]
        assert container.run_service.get_run(run["run_id"])["input"]["snapshot"]["model"]["model"] == "gpt-4o-mini"
        (tmp_path / "config.yaml").write_text(CONFIG.replace("id: assistant", "id: replacement"))
        with pytest.raises(ValueError):
            await service.send(group["id"], "下一轮", [], "2")
    asyncio.run(scenario())


def test_group_schedule_returns_context_error_without_creating_tasks():
    from server.infrastructure.tool_runtime import PlatformToolContext, _schedule_tool
    tool = _schedule_tool(None, PlatformToolContext(run_id="run_group", source="chat_group", agent_id="assistant", session_id="internal", metadata={}))
    result = json.loads(tool.invoke({"action": "create", "prompt": "提醒", "trigger_kind": "interval", "interval_minutes": 5}))
    assert result["ok"] is False
    assert "群聊暂不支持定时协作" in result["error"]


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_group_approval_resume_keeps_same_step_and_then_advances(tmp_path, monkeypatch, decision):
    from server.domain.run_approval import RunApprovalAction, RunApprovalInterrupt, RunApprovalRequest
    calls = []

    async def fake_run(self, **kwargs):
        calls.append(kwargs)
        if kwargs.get("resume") is None:
            return RunApprovalRequest(interrupts=(RunApprovalInterrupt(interrupt_id="group-approval", actions=(
                RunApprovalAction(name="write_file", args={"path": "/webdav/document.md"}, description="写入文档", allowed_decisions=("approve", "reject")),
            )),))
        return "已处理审批决定"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)

    async def scenario():
        container, service = setup(tmp_path)
        group = await service.create(definition())
        await service.send(group["id"], "检查文档", ["host", "review"], "approval")
        group = await execute_current(container, service, group["id"])
        assert group["executions"][-1]["status"] == "waiting_approval"
        run_id = group["active_run"]["run_id"]
        service = ChatGroupService(tmp_path, container.run_service, container.run_worker_service)
        await service.tick()
        assert len(container.run_service.list_runs()) == 1
        container.run_service.resume_run(run_id, decision=decision, message="按用户决定处理")
        await container.run_service.execute_run(run_id)
        await service.tick()
        assert service.read(group["id"])["messages"][-1]["content"] == "已处理审批决定"
        await service.tick()
        assert len(container.run_service.list_runs()) == 2
        assert calls[0]["thread_id"] == calls[1]["thread_id"]
        assert calls[1]["resume"].to_command_value()["group-approval"]["decisions"][0]["type"] == decision
    asyncio.run(scenario())
