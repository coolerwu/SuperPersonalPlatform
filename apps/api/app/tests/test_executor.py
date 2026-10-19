from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from app.services.executor import DeepAgentExecutor


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
