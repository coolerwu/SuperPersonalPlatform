"""Skill files are prompt material: Agent writes must pause for human approval."""

import asyncio

from server.infrastructure.agent_workspace import skill_write_permissions
from server.infrastructure.agent_filesystem_backend import AgentFilesystemBackend


def _request(tool_args: dict):
    return type("ToolRequest", (), {"tool_call": {"args": tool_args}})()


def test_skill_write_permissions_interrupt_only_for_skill_paths() -> None:
    from deepagents.middleware._fs_interrupt import _build_interrupt_on_from_permissions
    from deepagents.middleware.filesystem import _check_fs_permission

    permissions = skill_write_permissions()
    assert _check_fs_permission(permissions, "write", "/skills/demo/SKILL.md") == "interrupt"
    assert _check_fs_permission(permissions, "write", "/skills") == "interrupt"
    assert _check_fs_permission(permissions, "write", "/scratch/run.py") == "allow"
    assert _check_fs_permission(permissions, "read", "/skills/demo/SKILL.md") == "allow"

    interrupt_on = _build_interrupt_on_from_permissions(permissions)
    assert set(interrupt_on) == {"write_file", "edit_file", "delete"}
    write_when = interrupt_on["write_file"]["when"]
    assert write_when(_request({"file_path": "/skills/demo/SKILL.md"})) is True
    assert write_when(_request({"file_path": "/scratch/run.py"})) is False
    assert write_when(_request({})) is False
    assert interrupt_on["delete"]["when"](_request({"file_path": "/skills/demo"})) is True
    assert interrupt_on["delete"]["when"](_request({"file_path": "/artifacts/demo"})) is False


def test_file_approval_lease_never_covers_skills(tmp_path) -> None:
    from server.infrastructure.file_approval import FileApprovalStore, approval_file

    assert approval_file("write_file", {"file_path": "/skills/demo/SKILL.md"}) is None
    store = FileApprovalStore(tmp_path / "file_approvals.json", "assistant")
    store.grant("/webdav/notes/demo.md")
    assert store.allows("write_file", {"file_path": "/webdav/notes/demo.md"}) is True
    assert store.allows("write_file", {"file_path": "/skills/demo/SKILL.md"}) is False


def test_real_graph_skill_write_waits_for_approval(tmp_path) -> None:
    from deepagents import create_deep_agent
    from deepagents.middleware._fs_interrupt import _build_interrupt_on_from_permissions
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    workspace = tmp_path / "workspace"
    for directory in ("artifacts", "scratch", "skills", "memories"):
        (workspace / directory).mkdir(parents=True)
    skill_file = workspace / "skills" / "demo" / "SKILL.md"

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "skill-hitl-test"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if any(isinstance(message, ToolMessage) for message in messages):
                result = AIMessage(content="done")
            else:
                result = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "id": "write",
                            "args": {"file_path": "/skills/demo/SKILL.md", "content": "# demo"},
                        }
                    ],
                )
            return ChatResult(generations=[ChatGeneration(message=result)])

    permissions = skill_write_permissions()
    interrupt_on = _build_interrupt_on_from_permissions(permissions)
    for rule in interrupt_on.values():
        rule["allowed_decisions"] = ["approve", "reject"]
    saver = InMemorySaver()

    def build():
        return create_deep_agent(
            model=Model(),
            backend=AgentFilesystemBackend(root_dir=workspace, virtual_mode=True),
            permissions=permissions,
            interrupt_on=interrupt_on,
            checkpointer=saver,
        )

    rejected_config = {"configurable": {"thread_id": "skill-reject"}}
    first = asyncio.run(build().ainvoke({"messages": [{"role": "user", "content": "新建技能"}]}, rejected_config))
    assert first.get("__interrupt__")
    assert not skill_file.exists()
    asyncio.run(build().ainvoke(Command(resume={"decisions": [{"type": "reject"}]}), rejected_config))
    assert not skill_file.exists()

    approved_config = {"configurable": {"thread_id": "skill-approve"}}
    asyncio.run(build().ainvoke({"messages": [{"role": "user", "content": "再试一次"}]}, approved_config))
    asyncio.run(build().ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), approved_config))
    assert skill_file.read_text(encoding="utf-8") == "# demo"
