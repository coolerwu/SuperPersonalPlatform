from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.services.executor import (
    INTERRUPTED_TOOL_RESULT,
    RESUME_PROMPT,
    DeepAgentExecutor,
    _fresh_input,
    _has_run_input,
    _resume_input,
)


class _State(TypedDict):
    messages: Annotated[list[Any], add_messages]


def _interrupted_node(_state: _State) -> dict[str, Any]:
    return {
        "messages": [
            AIMessage(
                content="已经读取了一部分。",
                id="assistant-partial",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/a"},
                        "id": "call-read",
                    }
                ],
            )
        ]
    }


@pytest.mark.asyncio
async def test_resume_keeps_one_user_turn_and_closes_dangling_tools(tmp_path) -> None:
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "resume.sqlite")) as saver:
        graph = (
            StateGraph(_State)
            .add_node("respond", _interrupted_node)
            .add_edge(START, "respond")
            .add_edge("respond", END)
            .compile(checkpointer=saver)
        )
        config = {"configurable": {"thread_id": "session-1"}}
        fresh = _fresh_input("原始任务", "run-1")
        await graph.ainvoke({"messages": fresh["messages"]}, config)

        snapshot = await graph.aget_state(config)
        messages = list(snapshot.values["messages"])
        assert _has_run_input(messages, "run-1")
        assert [m.content for m in messages if isinstance(m, HumanMessage)] == ["原始任务"]
        assert any(isinstance(m, AIMessage) and m.tool_calls for m in messages)

        resumed = _resume_input(messages, "run-1")
        assert resumed["messages"][-1].content == RESUME_PROMPT
        assert all(
            not (isinstance(m, HumanMessage) and m.content == "原始任务")
            for m in resumed["messages"]
        )
        repairs = [m for m in resumed["messages"] if isinstance(m, ToolMessage)]
        assert [m.tool_call_id for m in repairs] == ["call-read"]
        assert repairs[0].content == INTERRUPTED_TOOL_RESULT
        assert repairs[0].status == "error"

        await graph.ainvoke({"messages": resumed["messages"]}, config)
        final = list((await graph.aget_state(config)).values["messages"])

    assert [m.content for m in final if isinstance(m, HumanMessage)] == [
        "原始任务",
        RESUME_PROMPT,
    ]


def test_fresh_input_is_used_when_checkpoint_lacks_the_run() -> None:
    messages = [
        HumanMessage(content="上一轮任务", id="run-old"),
        AIMessage(content="上一轮的答案"),
    ]
    assert not _has_run_input(messages, "run-new")
    fresh = _fresh_input("新的任务", "run-new")
    assert fresh["messages"][0].content == "新的任务"
    assert fresh["messages"][0].id == "run-new"


@pytest.mark.asyncio
async def test_executor_passes_resume_prompt_instead_of_original_input(
    tmp_path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    class FakeChatModel:
        def __init__(self, **_kwargs) -> None:
            pass

    class FakeAgent:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        HumanMessage(content="原始任务", id="run-1"),
                        AIMessage(content="处理到一半"),
                    ]
                }
            )

        async def astream(self, state, config=None, *, stream_mode=None, subgraphs=False):
            captured["state"] = state
            yield ((), "messages", (AIMessageChunk(content="继续完成"), {}))

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
        run_id="run-1",
    )
    events = [
        event
        async for event in executor.run(
            instructions="test", session_id="resume-session", content="原始任务"
        )
    ]

    assert any(event.type == "run_completed" for event in events), [
        (event.type, event.payload) for event in events
    ]
    humans = [
        message
        for message in captured["state"]["messages"]
        if isinstance(message, HumanMessage)
    ]
    assert [message.content for message in humans] == [RESUME_PROMPT]
