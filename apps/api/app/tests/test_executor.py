import base64
import logging
import shutil
from types import SimpleNamespace

import pytest
from langchain_core.exceptions import ModelAuthenticationError, ModelRateLimitError
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from app.config import settings
from app.services.executor import DeepAgentExecutor
from app.services.model_retry import log_retryable as _log_retryable


@pytest.mark.asyncio
async def test_executor_fails_without_api_key(tmp_path) -> None:
    provider = SimpleNamespace(api_key="", base_url="https://api.deepseek.com", model="deepseek-v4-flash")
    executor = DeepAgentExecutor(provider=provider, checkpoint_dir=tmp_path, agent_name="test")
    events = [
        event
        async for event in executor.run(instructions="test", session_id="no-key", content="hi")
    ]
    assert events
    assert events[0].type == "run_failed"
    assert events[0].payload["error"]["type"] == "ProviderError"


@pytest.mark.asyncio
async def test_executor_emits_browser_action_for_browser_tool_results(
    tmp_path, monkeypatch
) -> None:
    class FakeAgent:
        async def astream(self, _state, config=None, *, stream_mode=None, subgraphs=False):
            yield (
                ("tools",),
                "messages",
                (
                    ToolMessage(
                        content="Opened https://example.com in tab 'main' (HTTP 200)",
                        tool_call_id="call-browser",
                        name="browser_open",
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content="file contents",
                        tool_call_id="call-file",
                        name="read_file",
                    ),
                    {},
                ),
            )
            yield ((), "messages", (AIMessageChunk(content="done"), {}))

    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: FakeAgent())
    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="test-agent",
    )
    events = [
        event
        async for event in executor.run(instructions="test", session_id="browser", content="hi")
    ]

    browser_events = [event for event in events if event.type == "browser_action"]
    assert len(browser_events) == 1
    payload = browser_events[0].payload
    assert payload["tool"] == "browser_open"
    assert payload["ok"] is True
    assert payload["agent"] == "tools"
    assert "Opened https://example.com" in payload["summary"]
    assert any(event.type == "assistant_delta" for event in events)
    assert any(event.type == "run_completed" for event in events)


@pytest.mark.asyncio
async def test_executor_announces_tool_calls_with_parsed_arguments(tmp_path, monkeypatch) -> None:
    class FakeAgent:
        async def astream(self, _state, config=None, *, stream_mode=None, subgraphs=False):
            yield (
                (),
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "read_file",
                                "args": '{"file_path": "upl',
                                "id": "call-read",
                                "index": 0,
                            }
                        ],
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {"name": None, "args": 'oads/a.pdf"}', "id": "call-read", "index": 0}
                        ],
                    ),
                    {},
                ),
            )
            yield ((), "messages", (AIMessageChunk(content="done"), {}))

    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: FakeAgent())
    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="test-agent",
    )
    events = [
        event
        async for event in executor.run(instructions="test", session_id="tool-call", content="hi")
    ]

    tool_calls = [event for event in events if event.type == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].payload["tool"] == "read_file"
    assert tool_calls[0].payload["args"] == {"file_path": "uploads/a.pdf"}
    assert tool_calls[0].payload["agent"] == "test"


@pytest.mark.asyncio
async def test_executor_always_mounts_host_shell_and_interpreter(
    tmp_path, monkeypatch
) -> None:
    captured: dict = {}

    class FakeAgent:
        async def astream(self, _state, config=None, *, stream_mode=None, subgraphs=False):
            yield ((), "messages", (AIMessageChunk(content="done"), {}))

    def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeAgent()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create)
    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )

    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="execution-agent",
        enabled_skills=(),
    )
    events = [
        event
        async for event in executor.run(
            instructions="test", session_id="exec-stack", content="hi"
        )
    ]
    assert any(event.type == "run_completed" for event in events)

    from deepagents.backends import CompositeBackend, LocalShellBackend

    assert isinstance(captured["backend"], CompositeBackend)
    assert isinstance(captured["backend"].default, LocalShellBackend)
    middleware_names = [type(item).__name__ for item in captured["middleware"]]
    assert "CodeInterpreterMiddleware" in middleware_names


@pytest.mark.asyncio
async def test_executor_retries_on_the_model_for_subagents(tmp_path, monkeypatch) -> None:
    captured: dict = {}

    class FakeChatModel:
        def __init__(self, **kwargs) -> None:
            captured["chat"] = kwargs

    class FakeAgent:
        async def astream(self, _state, config=None, *, stream_mode=None, subgraphs=False):
            yield ((), "messages", (AIMessageChunk(content="done"), {}))

    def fake_create(**kwargs):
        captured["middleware"] = kwargs["middleware"]
        captured["model"] = kwargs["model"]
        return FakeAgent()

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatModel)
    monkeypatch.setattr("deepagents.create_deep_agent", fake_create)

    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="test-agent",
    )
    events = [
        event
        async for event in executor.run(instructions="test", session_id="retry", content="hi")
    ]

    assert captured["chat"]["max_retries"] == 0
    assert captured["model"].retry_max_attempts == 3
    assert captured["model"].retry_agent_name == "test"
    assert captured["model"].retry_session_id == "retry"
    assert all(item.name != "ModelRetryMiddleware" for item in captured["middleware"])
    assert any(event.type == "run_completed" for event in events)


def test_retryable_model_errors_are_logged(caplog) -> None:
    caplog.set_level(logging.WARNING)

    assert _log_retryable(
        ModelRateLimitError("429"), agent_name="agent", session_id="session"
    ) is True
    assert "model call failed (retryable)" in caplog.text
    assert "ModelRateLimitError" in caplog.text

    caplog.clear()
    assert _log_retryable(
        ModelAuthenticationError("401"), agent_name="agent", session_id="session"
    ) is False
    assert "model call failed (retryable)" not in caplog.text


@pytest.mark.asyncio
async def test_executor_emits_reasoning_deltas(tmp_path, monkeypatch) -> None:
    class FakeAgent:
        async def astream(self, _state, config=None, *, stream_mode=None, subgraphs=False):
            yield (
                (),
                "messages",
                (
                    AIMessageChunk(
                        content="answer",
                        additional_kwargs={"reasoning_content": "thinking"},
                    ),
                    {},
                ),
            )

    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: FakeAgent())
    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="reasoning-agent",
    )

    events = [
        event
        async for event in executor.run(instructions="test", session_id="e" * 32, content="hi")
    ]

    assert [
        event.payload["delta"] for event in events if event.type == "reasoning_delta"
    ] == ["thinking"]
    assert any(
        event.type == "assistant_delta" and event.payload["delta"] == "answer"
        for event in events
    )
    shutil.rmtree(settings.workspaces_dir / "reasoning-agent", ignore_errors=True)


@pytest.mark.asyncio
async def test_executor_saves_browser_screenshot(tmp_path, monkeypatch) -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
    )
    encoded = base64.b64encode(png).decode()

    class FakeAgent:
        async def astream(self, _state, config=None, *, stream_mode=None, subgraphs=False):
            yield (
                ("tools",),
                "messages",
                (
                    ToolMessage(
                        content=[
                            {
                                "type": "text",
                                "text": "Screenshot of tab 'main' at https://example.com",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{encoded}"},
                            },
                        ],
                        tool_call_id="call-shot",
                        name="browser_screenshot",
                    ),
                    {},
                ),
            )
            yield ((), "messages", (AIMessageChunk(content="done"), {}))

    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: FakeAgent())
    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )
    session_id = "a" * 32
    run_id = "b" * 32
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="screenshot-agent",
        run_id=run_id,
    )

    events = [
        event
        async for event in executor.run(instructions="test", session_id=session_id, content="hi")
    ]

    shots = [
        event
        for event in events
        if event.type == "browser_action" and event.payload.get("tool") == "browser_screenshot"
    ]
    assert len(shots) == 1
    screenshot_id = shots[0].payload["screenshot_id"]
    path = (
        settings.workspaces_dir
        / "screenshot-agent"
        / "screenshots"
        / session_id
        / run_id
        / f"{screenshot_id}.png"
    )
    assert path.read_bytes() == png
    shutil.rmtree(settings.workspaces_dir / "screenshot-agent", ignore_errors=True)


@pytest.mark.asyncio
async def test_executor_gives_final_answer_when_step_limit_reached(tmp_path, monkeypatch) -> None:
    captured: dict = {"states": []}

    class FakeChatModel:
        def __init__(self, **kwargs) -> None:
            captured["chat"] = kwargs

        async def ainvoke(self, messages, config=None):
            captured["final_messages"] = messages
            return AIMessage(content="已完成：读取页面；未完成：提交表单。")

    class FakeAgent:
        calls = 0

        async def astream(self, state, config=None, *, stream_mode=None, subgraphs=False):
            FakeAgent.calls += 1
            captured["config"] = config
            captured["states"].append(state)
            yield ((), "messages", (AIMessageChunk(content="working "), {}))
            raise GraphRecursionError("Recursion limit reached")

        async def aget_state(self, config):
            return SimpleNamespace(
                values={
                    "messages": [
                        HumanMessage(content="帮我查天气并提交表单"),
                        ToolMessage(content="sunny", tool_call_id="call-1", name="web_search"),
                        AIMessage(
                            content="",
                            tool_calls=[{"name": "web_fetch", "args": {}, "id": "call-2"}],
                        ),
                    ]
                }
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatModel)
    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: FakeAgent())

    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="test-agent",
    )
    events = [
        event
        async for event in executor.run(instructions="test", session_id="limit", content="查天气")
    ]

    assert captured["config"]["recursion_limit"] == settings.run_recursion_limit
    assert FakeAgent.calls == 2
    assert "继续完成原任务" in captured["states"][1]["messages"][0].content
    final_messages = captured["final_messages"]
    assert isinstance(final_messages[0], SystemMessage)
    assert any(
        isinstance(message, HumanMessage) and "帮我查天气并提交表单" in message.content
        for message in final_messages
    )
    assert any(
        isinstance(message, ToolMessage) and "sunny" in message.content
        for message in final_messages
    )
    assert isinstance(final_messages[-1], HumanMessage)
    assert "最终答复" in final_messages[-1].content
    assert not any(
        isinstance(message, AIMessage) and getattr(message, "tool_calls", None)
        for message in final_messages
    )
    step_events = [event for event in events if event.type == "step_limit"]
    assert [event.payload["action"] for event in step_events] == ["continue", "summary"]
    assert not any(event.type == "run_failed" for event in events)
    completed = [event for event in events if event.type == "run_completed"]
    assert len(completed) == 1
    output = completed[0].payload["output"]
    assert output.startswith("（已达到执行步骤上限")
    assert "已完成：读取页面" in output


@pytest.mark.asyncio
async def test_executor_continues_after_step_limit(tmp_path, monkeypatch) -> None:
    captured: dict = {"states": [], "summary_calls": 0}

    class FakeChatModel:
        def __init__(self, **kwargs) -> None:
            captured["chat"] = kwargs

        async def ainvoke(self, messages, config=None):
            captured["summary_calls"] += 1
            return AIMessage(content="summary")

    class FakeAgent:
        calls = 0

        async def astream(self, state, config=None, *, stream_mode=None, subgraphs=False):
            FakeAgent.calls += 1
            captured["states"].append(state)
            if FakeAgent.calls == 1:
                yield ((), "messages", (AIMessageChunk(content="第一步 "), {}))
                raise GraphRecursionError("Recursion limit reached")
            yield ((), "messages", (AIMessageChunk(content="完成"), {}))

        async def aget_state(self, config):
            raise AssertionError("summary must not run when the continuation succeeds")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatModel)
    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: FakeAgent())

    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="test-agent",
    )
    events = [
        event
        async for event in executor.run(instructions="test", session_id="continue", content="查天气")
    ]

    assert FakeAgent.calls == 2
    assert captured["summary_calls"] == 0
    assert "继续完成原任务" in captured["states"][1]["messages"][0].content
    assert "skills_metadata" not in captured["states"][1]
    step_events = [event for event in events if event.type == "step_limit"]
    assert [event.payload["action"] for event in step_events] == ["continue"]
    assert not any(event.type == "run_failed" for event in events)
    completed = [event for event in events if event.type == "run_completed"]
    assert len(completed) == 1
    assert completed[0].payload["output"] == "第一步 完成"


@pytest.mark.asyncio
async def test_executor_hides_summarization_chunks(tmp_path, monkeypatch) -> None:
    class FakeAgent:
        async def astream(self, _state, config=None, *, stream_mode=None, subgraphs=False):
            yield (
                (),
                "messages",
                (
                    AIMessageChunk(content="## SESSION INTENT\ninternal summary"),
                    {"lc_source": "summarization", "langgraph_node": "model"},
                ),
            )
            yield (
                (),
                "messages",
                (
                    AIMessageChunk(content="visible answer"),
                    {"langgraph_node": "model"},
                ),
            )

    monkeypatch.setattr("deepagents.create_deep_agent", lambda **_kwargs: FakeAgent())

    provider = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
    )
    executor = DeepAgentExecutor(
        provider=provider,
        checkpoint_dir=tmp_path,
        agent_name="test",
        workspace_path="test-agent",
    )
    events = [
        event
        async for event in executor.run(instructions="test", session_id="summary", content="hi")
    ]

    deltas = [event.payload["delta"] for event in events if event.type == "assistant_delta"]
    assert deltas == ["visible answer"]
    completed = [event for event in events if event.type == "run_completed"]
    assert len(completed) == 1
    assert completed[0].payload["output"] == "visible answer"
