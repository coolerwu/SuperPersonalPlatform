from __future__ import annotations

from types import SimpleNamespace

import pytest
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


def test_browser_skill_unlocks_all_browser_tools() -> None:
    from deepagents.backends import StateBackend

    from app.services.browser_tools import BROWSER_TOOL_NAMES, register_browser_tools
    from app.services.skill_tools import SkillToolContext, SkillToolRegistry

    registry = SkillToolRegistry()
    register_browser_tools(registry)
    tools = registry.resolve_names(
        sorted(BROWSER_TOOL_NAMES),
        SkillToolContext(api_key="", base_url="", browser=None),
    )
    middleware = AgentSkillsMiddleware(
        backend=StateBackend(),
        sources=[("/skills/", "Test")],
        enabled_skills={"web-browser"},
        tools=tools,
    )
    metadata = [_skill("web-browser", " ".join(sorted(BROWSER_TOOL_NAMES)))]

    assert _visible_tools(middleware, [HumanMessage(content="hi")], metadata) == []
    visible = _visible_tools(
        middleware, _read_messages("/skills/web-browser/SKILL.md"), metadata
    )
    assert set(visible) == BROWSER_TOOL_NAMES


def _gated_middleware() -> AgentSkillsMiddleware:
    from deepagents.backends import StateBackend

    return AgentSkillsMiddleware(
        backend=StateBackend(),
        sources=[("/skills/", "Test")],
        enabled_skills={"local-shell"},
        tools=[],
        gated_tools={"execute": "local-shell"},
    )


def _visible_gated_tools(middleware: AgentSkillsMiddleware, messages: list, metadata: list[dict]) -> list[str]:
    seen: list[str] = []

    def handler(request: ModelRequest) -> AIMessage:
        seen.extend(str(getattr(tool, "name", tool)) for tool in request.tools)
        return AIMessage(content="ok")

    tool = StructuredTool.from_function(lambda command: command, name="execute", description="exec")
    middleware.wrap_model_call(
        ModelRequest(
            model=SimpleNamespace(),
            messages=messages,
            tools=[tool],
            state={"skills_metadata": metadata, "messages": messages},
        ),
        handler,
    )
    return seen


def test_host_shell_tool_stays_hidden_until_skill_is_read() -> None:
    middleware = _gated_middleware()
    metadata = [_skill("local-shell", "execute")]

    assert _visible_gated_tools(middleware, [HumanMessage(content="hi")], metadata) == []
    assert _visible_gated_tools(
        middleware, _read_messages("/skills/local-shell/SKILL.md"), metadata
    ) == ["execute"]


def test_host_shell_call_is_denied_until_skill_is_read() -> None:
    middleware = _gated_middleware()
    metadata = [_skill("local-shell", "execute")]

    def call(messages: list) -> tuple[bool, ToolMessage]:
        called = False

        def handler(_request) -> ToolMessage:
            nonlocal called
            called = True
            return ToolMessage(content="ran", tool_call_id="call-exec")

        result = middleware.wrap_tool_call(
            SimpleNamespace(
                tool_call={"name": "execute", "args": {"command": "pwd"}, "id": "call-exec"},
                tool=None,
                state={"skills_metadata": metadata, "messages": messages},
            ),
            handler,
        )
        return called, result

    called, result = call([HumanMessage(content="hi")])
    assert called is False
    assert result.status == "error"
    assert "locked" in result.content

    called, result = call(_read_messages("/skills/local-shell/SKILL.md"))
    assert called is True
    assert result.content == "ran"


@pytest.mark.asyncio
async def test_host_shell_gate_applies_to_async_model_and_tool_calls() -> None:
    middleware = _gated_middleware()
    metadata = [_skill("local-shell", "execute")]
    tool = StructuredTool.from_function(lambda command: command, name="execute", description="exec")
    seen: list[str] = []

    async def model_handler(request: ModelRequest) -> AIMessage:
        seen.extend(str(getattr(item, "name", item)) for item in request.tools)
        return AIMessage(content="ok")

    async def run_model(messages: list) -> None:
        await middleware.awrap_model_call(
            ModelRequest(
                model=SimpleNamespace(),
                messages=messages,
                tools=[tool],
                state={"skills_metadata": metadata, "messages": messages},
            ),
            model_handler,
        )

    await run_model([HumanMessage(content="hi")])
    assert seen == []
    await run_model(_read_messages("/skills/local-shell/SKILL.md"))
    assert seen == ["execute"]

    async def tool_handler(_request) -> ToolMessage:
        return ToolMessage(content="ran", tool_call_id="call-exec")

    async def run_tool(messages: list) -> ToolMessage:
        return await middleware.awrap_tool_call(
            SimpleNamespace(
                tool_call={"name": "execute", "args": {"command": "pwd"}, "id": "call-exec"},
                tool=None,
                state={"skills_metadata": metadata, "messages": messages},
            ),
            tool_handler,
        )

    denied = await run_tool([HumanMessage(content="hi")])
    assert denied.status == "error"
    allowed = await run_tool(_read_messages("/skills/local-shell/SKILL.md"))
    assert allowed.content == "ran"
