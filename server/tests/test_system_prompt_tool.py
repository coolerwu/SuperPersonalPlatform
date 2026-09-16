import asyncio
import json

from server.app.config_file_service import describe_system_prompt_update
from server.domain.agent_config import ModelDefinition
from server.domain.run_approval import RunApprovalDecision, RunApprovalRequest, RunApprovalResume
from server.domain.tooling import (
    ALWAYS_ON_APPROVAL_TOOL_IDS,
    SYSTEM_APPROVAL_TOOL_IDS,
    get_tool_definition,
)
from server.infrastructure.deepagent_runtime import (
    DeepAgentRuntime,
    DeepAgentRuntimeOptions,
    RuntimeMessage,
)
from server.infrastructure.tool_runtime import PlatformToolContext, build_platform_tools


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


def _tool_context() -> PlatformToolContext:
    return PlatformToolContext(
        run_id="run_1",
        source="web_chat",
        agent_id="assistant",
        session_id="session_1",
        metadata={},
    )


def test_registry_marks_system_prompt_tool_as_always_on_approval_tool() -> None:
    definition = get_tool_definition("update_system_prompt")

    assert definition.approval_required is True
    assert definition.always_on is True
    assert ALWAYS_ON_APPROVAL_TOOL_IDS == ("update_system_prompt",)
    assert "update_system_prompt" not in SYSTEM_APPROVAL_TOOL_IDS
    # Always-on approval tools keep a resumable checkpoint even without authorized tools.
    assert DeepAgentRuntimeOptions().interrupt_on == ("update_system_prompt",)
    assert DeepAgentRuntimeOptions(self_config=False).interrupt_on == ()


def test_build_platform_tools_injects_self_config_tool_only_when_allowed(tmp_path) -> None:
    authorized_only = build_platform_tools(
        ("update_system_prompt",),
        context_workspace=tmp_path / "context",
        tool_context=_tool_context(),
    )
    without_context = build_platform_tools(
        (),
        context_workspace=tmp_path / "context",
        include_self_config=True,
    )
    with_context = build_platform_tools(
        (),
        context_workspace=tmp_path / "context",
        tool_context=_tool_context(),
        include_self_config=True,
    )

    assert authorized_only == []
    assert without_context == []
    assert [tool.name for tool in with_context] == ["update_system_prompt"]


def test_update_system_prompt_tool_writes_config_and_reports_errors(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in build_platform_tools(
            (),
            context_workspace=tmp_path / "context",
            tool_context=_tool_context(),
            include_self_config=True,
        )
    }
    tool = tools["update_system_prompt"]

    applied = json.loads(tool.invoke({"new_prompt": "你是新人格。", "reason": "用户要求"}))

    assert applied == {
        "ok": True,
        "tool": "update_system_prompt",
        "agent_id": "assistant",
        "status": "applied",
        "length": len("你是新人格。"),
        "message": "系统提示词已更新，将在下一次运行生效",
    }
    assert "你是新人格。" in (tmp_path / "config.yaml").read_text(encoding="utf-8")

    empty = json.loads(tool.invoke({"new_prompt": "   "}))
    missing_agent = json.loads(
        build_platform_tools(
            (),
            context_workspace=tmp_path / "context",
            tool_context=PlatformToolContext(
                run_id="run_1",
                source="web_chat",
                agent_id="missing",
                session_id="",
                metadata={},
            ),
            include_self_config=True,
        )[0].invoke({"new_prompt": "新人格"})
    )

    assert empty["ok"] is False
    assert empty["error"]["type"] == "AgentPromptUpdateError"
    assert missing_agent["ok"] is False
    assert missing_agent["error"]["type"] == "AgentPromptUpdateError"
    assert "你是新人格。" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_describe_system_prompt_update_compares_versions_and_never_raises(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    description = describe_system_prompt_update(
        tmp_path / "config.yaml",
        "assistant",
        {"new_prompt": "你是新人格。", "reason": "用户要求"},
    )

    assert "Agent「Assistant（assistant）」" in description
    assert "理由：用户要求" in description
    assert "拟修改为（新）：\n你是新人格。" in description
    assert "当前（旧）：\nBe direct." in description

    missing_config = describe_system_prompt_update(
        tmp_path / "missing" / "config.yaml",
        "assistant",
        {"new_prompt": "你是新人格。"},
    )
    broken_args = describe_system_prompt_update(tmp_path / "config.yaml", "assistant", None)

    assert "读取当前 config.yaml 失败" in missing_config
    assert "（未提供新的提示词文本）" in broken_args


def _prompt_edit_model():
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "system-prompt-approval-test"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if any(isinstance(message, ToolMessage) for message in messages):
                message = AIMessage(content="done")
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_system_prompt",
                            "id": "edit-prompt",
                            "args": {"new_prompt": "你是新人格。", "reason": "用户要求"},
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return Model()


def _runtime(tmp_path, monkeypatch, agent_id: str = "assistant") -> DeepAgentRuntime:
    model = _prompt_edit_model()
    monkeypatch.setattr(DeepAgentRuntime, "_chat_model", lambda self: model)
    return DeepAgentRuntime(
        ModelDefinition(
            id="default",
            name="Default",
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o-mini",
        ),
        context_workspace=tmp_path / "context",
        agent_id=agent_id,
        agent_workspace=tmp_path / "agents" / agent_id / "workspace",
        tool_context=PlatformToolContext(
            run_id="run_1",
            source="web_chat",
            agent_id=agent_id,
            session_id="session_1",
            metadata={},
        ),
    )


def test_runtime_routes_system_prompt_tool_through_hitl(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    runtime = _runtime(tmp_path, monkeypatch)
    checkpoint = tmp_path / "runs" / "run_1" / "checkpoints.sqlite"
    options = DeepAgentRuntimeOptions()

    async def scenario():
        approval = await runtime.run(
            instructions="Be direct.",
            messages=(RuntimeMessage(role="user", content="把你的系统提示词改成新人格"),),
            options=options,
            checkpoint_path=checkpoint,
            thread_id="run_1",
        )
        assert isinstance(approval, RunApprovalRequest)
        action = approval.interrupts[0].actions[0]
        assert action.name == "update_system_prompt"
        assert action.allowed_decisions == ("approve", "reject")
        assert action.args["new_prompt"] == "你是新人格。"
        assert "拟修改为（新）：\n你是新人格。" in action.description
        assert "当前（旧）：\nBe direct." in action.description
        assert "Be direct." in (tmp_path / "config.yaml").read_text(encoding="utf-8")

        approved = await runtime.run(
            instructions="Be direct.",
            messages=(),
            options=options,
            checkpoint_path=checkpoint,
            thread_id="run_1",
            resume=RunApprovalResume(
                values=(
                    (
                        approval.interrupts[0].interrupt_id,
                        (RunApprovalDecision(type="approve"),),
                    ),
                )
            ),
        )
        return approved

    assert asyncio.run(scenario()) == "done"
    assert "你是新人格。" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "Be direct." not in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_runtime_rejected_system_prompt_update_keeps_config(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    runtime = _runtime(tmp_path, monkeypatch)
    checkpoint = tmp_path / "runs" / "run_2" / "checkpoints.sqlite"
    options = DeepAgentRuntimeOptions()

    async def scenario():
        approval = await runtime.run(
            instructions="Be direct.",
            messages=(RuntimeMessage(role="user", content="改人格"),),
            options=options,
            checkpoint_path=checkpoint,
            thread_id="run_2",
        )
        assert isinstance(approval, RunApprovalRequest)
        rejected = await runtime.run(
            instructions="Be direct.",
            messages=(),
            options=options,
            checkpoint_path=checkpoint,
            thread_id="run_2",
            resume=RunApprovalResume(
                values=(
                    (
                        approval.interrupts[0].interrupt_id,
                        (RunApprovalDecision(type="reject", message="不要改"),),
                    ),
                )
            ),
        )
        return rejected

    assert asyncio.run(scenario()) == "done"
    assert "Be direct." in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "你是新人格。" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_runtime_skips_self_config_tool_for_group_runs(tmp_path, monkeypatch) -> None:
    import deepagents

    captured = {}

    class FakeAgent:
        async def ainvoke(self, state, config):
            return {"messages": [type("Message", (), {"content": "ok"})()]}

    monkeypatch.setattr(deepagents, "create_deep_agent", lambda **kwargs: captured.update(kwargs) or FakeAgent())
    runtime = _runtime(tmp_path, monkeypatch)

    asyncio.run(
        runtime.run(
            instructions="主持",
            messages=(RuntimeMessage(role="user", content="任务"),),
            options=DeepAgentRuntimeOptions(self_config=False, group_control={
                "run_id": "run_group",
                "control_members": ["host"],
                "finish_only": False,
            }),
        )
    )

    tool_names = [tool.name for tool in captured["tools"]]
    assert "update_system_prompt" not in tool_names
    assert tool_names == ["group_decision"]
    assert "update_system_prompt" not in [tool.name for tool in captured["subagents"][0]["tools"]]
    assert "update_system_prompt" not in captured.get("interrupt_on", {})
