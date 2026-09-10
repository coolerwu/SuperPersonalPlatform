import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from server.app.run_usage import add_call, summarize_usage
from server.app.run_service import RunService
from server.domain.run_events import ModelUsagePayload, RunEventType
from server.infrastructure.config import parse_model_definition
from server.infrastructure.deepagent_runtime import DeepAgentStreamEvent, _usage_callback
from server.tests.test_run_service import CONFIG


def model(**kwargs):
    return parse_model_definition(dict(id="test", name="test", base_url="https://example.com", api_key="test", model="test", **kwargs))


def test_prices_validate_and_zero_is_valid():
    for price in (-1, float("nan"), float("inf"), "1", True):
        with pytest.raises(ValueError):
            model(input_price_per_million=price)
    assert model(input_price_per_million=0).input_price_per_million == 0


def test_usage_dedup_unknown_and_currency_separation():
    ledger = {}
    call = ModelUsagePayload("one", "test", 1000, 500, 200)
    add_call(ledger, call, model(input_price_per_million=2, output_price_per_million=4))
    add_call(ledger, call, model(input_price_per_million=99))
    add_call(ledger, ModelUsagePayload("two", "test", 1000, 500), model(input_price_per_million=0, output_price_per_million=0, price_currency="CNY"))
    add_call(ledger, ModelUsagePayload("three", "test", error=True), model())
    summary = summarize_usage(ledger)
    assert summary["model_calls"] == 3
    assert summary["input_tokens"] == 2000
    assert summary["cached_input_tokens"] == 200
    assert summary["unknown_calls"] == summary["unpriced_calls"] == 1
    assert summary["estimated_costs"] == {"USD": .004, "CNY": 0}
    assert summarize_usage({})["input_tokens"] is None


def test_callback_usage_missing_and_errors():
    events = []
    callback = _usage_callback(events.append, "configured")
    response = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok", usage_metadata={
        "input_tokens": 50, "output_tokens": 10, "total_tokens": 60,
        "input_token_details": {"cache_read": 20},
    }, response_metadata={"model_name": "actual"}))]])
    asyncio.run(callback.on_llm_end(response, run_id=uuid4()))
    asyncio.run(callback.on_llm_end(LLMResult(generations=[]), run_id=uuid4()))
    asyncio.run(callback.on_llm_error(ValueError("private error"), run_id=uuid4()))
    assert events[0].payload.model == "actual"
    assert events[0].payload.input_tokens == 50
    assert events[0].payload.cached_input_tokens == 20
    assert events[1].payload.input_tokens is None
    assert events[2].payload.error is True
    assert "private error" not in str(events)


def test_usage_persists_failure_retry_rerun_and_restart(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG.replace("model: gpt-4o-mini", "model: gpt-4o-mini\n      input_price_per_million: 2\n      output_price_per_million: 4"))
    attempts = 0

    async def fake_run(self, **kwargs):
        nonlocal attempts
        attempts += 1
        kwargs["stream_callback"](DeepAgentStreamEvent(RunEventType.MODEL_USAGE, ModelUsagePayload(str(attempts), "test", 1000, 500)))
        if attempts == 1:
            raise ValueError("after model call")
        return "done"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)
    service = RunService(tmp_path)
    run_id = asyncio.run(service.create_run(content="hello", agent_id="assistant"))["run_id"]
    assert service.get_run(run_id)["usage"] is None
    with pytest.raises(ValueError):
        asyncio.run(service.execute_run(run_id))
    assert service.get_run(run_id)["usage"]["input_tokens"] == 1000
    completed = asyncio.run(service.execute_run(run_id))
    assert completed["usage"]["model_calls"] == 2
    assert completed["usage"]["estimated_costs"] == {"USD": .008}
    assert completed["usage"]["interrupted_segments"] == 0
    service.rerun(run_id)
    asyncio.run(service.execute_run(run_id))
    restarted = RunService(tmp_path)
    assert restarted.get_run(run_id)["usage"]["model_calls"] == 3
    assert restarted.list_runs()[0]["usage"]["input_tokens"] == 3000


def test_real_graph_callbacks_include_subgraph_without_history_double_count(tmp_path):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langgraph.graph import StateGraph, MessagesState, START, END
    from server.infrastructure.deepagent_runtime import DeepAgentRuntime

    fake = FakeMessagesListChatModel(responses=[AIMessage(content="ok", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})])

    async def call_model(state):
        return {"messages": [await fake.ainvoke(state["messages"])]}

    child = StateGraph(MessagesState)
    child.add_node("child_model", call_model)
    child.add_edge(START, "child_model")
    child.add_edge("child_model", END)
    graph = StateGraph(MessagesState)
    graph.add_node("main_model", call_model)
    graph.add_node("child", child.compile())
    graph.add_edge(START, "main_model")
    graph.add_edge("main_model", "child")
    graph.add_edge("child", END)
    events = []
    runtime = DeepAgentRuntime(model(), context_workspace=tmp_path, agent_workspace=tmp_path / "agent")
    asyncio.run(runtime._run_agent(graph.compile(), {"messages": [AIMessage(content="old", usage_metadata={"input_tokens": 999, "output_tokens": 999, "total_tokens": 1998})]}, {"callbacks": [_usage_callback(events.append, "test")]}, stream_callback=events.append))
    usage = [event.payload for event in events if event.type == RunEventType.MODEL_USAGE]
    assert len(usage) == 2
    assert sum(call.input_tokens for call in usage) == 20
    assert len({call.call_id for call in usage}) == 2
