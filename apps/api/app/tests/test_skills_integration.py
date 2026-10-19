from __future__ import annotations

from types import SimpleNamespace

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.services.skill_middleware import AgentSkillsMiddleware


def _skill(name: str, tool_name: str | None = None) -> dict:
    return {
        "name": name,
        "description": f"{name} skill",
        "path": f"/skills/{name}/SKILL.md",
        "metadata": {"include_tools": tool_name} if tool_name else {},
        "allowed_tools": [],
        "license": None,
        "compatibility": None,
    }


def _read_messages(path: str) -> list:
    call = AIMessage(
        content="",
        tool_calls=[
            {"name": "read_file", "args": {"file_path": path}, "id": f"call-{path}"}
        ],
    )
    return [
        HumanMessage(content="use the skill"),
        call,
        ToolMessage(content="skill body", tool_call_id=f"call-{path}"),
    ]


def _visible_tools(middleware: AgentSkillsMiddleware, messages: list, metadata: list[dict]) -> list[str]:
    seen: list[str] = []

    def handler(request: ModelRequest) -> AIMessage:
        seen.extend(str(getattr(tool, "name", tool)) for tool in request.tools)
        return AIMessage(content="ok")

    request = ModelRequest(
        model=SimpleNamespace(),
        messages=messages,
        tools=[],
        state={"skills_metadata": metadata, "messages": messages},
    )
    middleware.wrap_model_call(request, handler)
    return seen


def _middleware(enabled: set[str], tool: StructuredTool) -> AgentSkillsMiddleware:
    from deepagents.backends import StateBackend

    return AgentSkillsMiddleware(
        backend=StateBackend(),
        sources=[("/skills/", "Test")],
        enabled_skills=enabled,
        tools=[tool],
    )


def test_skill_tools_stay_locked_until_skill_is_read() -> None:
    fake = StructuredTool.from_function(lambda text: text, name="fake_tool", description="fake")
    middleware = _middleware({"demo"}, fake)
    metadata = [_skill("demo", "fake_tool")]

    assert _visible_tools(middleware, [HumanMessage(content="hi")], metadata) == []
    assert _visible_tools(middleware, _read_messages("/skills/demo/SKILL.md"), metadata) == ["fake_tool"]


def test_disabled_skill_read_does_not_unlock_tools() -> None:
    fake = StructuredTool.from_function(lambda text: text, name="fake_tool", description="fake")
    middleware = _middleware({"demo"}, fake)
    metadata = [_skill("demo", "fake_tool")]

    messages = _read_messages("/skills/other/SKILL.md")
    assert _visible_tools(middleware, messages, metadata) == []
