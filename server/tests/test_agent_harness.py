import asyncio
import importlib

import pytest

from server.domain.agents import AgentDefinition, ModelDefinition
from server.domain.harness import (
    Agent,
    AgentRunPhase,
    AgentToolCall,
    AgentToolReasoningResult,
    AgentToolResult,
    ChatOptions,
    HarnessMode,
    HarnessRequest,
    run_agent,
)


def test_harness_modes_have_separate_modules() -> None:
    prompt_module = importlib.import_module("server.domain.harness.prompt")
    tools_module = importlib.import_module("server.domain.harness.tools")

    assert callable(prompt_module.run_prompt_mode)
    assert callable(tools_module.run_tools_mode)
    assert tools_module.AgentRunPhase is AgentRunPhase


class FakeGateway:
    def __init__(self, reasoning_results=()) -> None:
        self.reasoning_results = list(reasoning_results)
        self.complete_calls = []
        self.reason_calls = []
        self.append_calls = []
        self.force_calls = []

    async def complete(self, model, system_prompt, content, images):
        self.complete_calls.append((model, system_prompt, content, images))
        return "prompt answer"

    async def reason_with_tools(
        self,
        model,
        system_prompt,
        content,
        tool_names,
        messages,
        images,
    ):
        self.reason_calls.append((model, system_prompt, content, tool_names, messages, images))
        return self.reasoning_results.pop(0)

    def append_tool_results(self, messages, results):
        self.append_calls.append((messages, results))
        return (*messages, *results)

    async def force_tool_final(self, model, messages):
        self.force_calls.append((model, messages))
        return "forced answer"


class FakeToolRegistry:
    def __init__(self) -> None:
        self.calls = []

    async def dispatch(self, name, args, runtime):
        self.calls.append((name, args, runtime))
        return f"result:{name}"


def make_agent(gateway) -> Agent:
    return Agent(
        definition=AgentDefinition(
            id="assistant",
            name="Assistant",
            system_prompt="Be concise.",
            model_id="fast",
        ),
        model=ModelDefinition(
            id="fast",
            name="Fast",
            base_url="https://example.test/v1",
            api_key="secret",
            model="fast-chat",
        ),
        llm_client=gateway,
    )


def test_prompt_mode_completes_without_tool_state() -> None:
    gateway = FakeGateway()

    result = asyncio.run(
        run_agent(
            make_agent(gateway),
            HarnessRequest(mode=HarnessMode.PROMPT, content="hello"),
        )
    )

    assert result == "prompt answer"
    assert len(gateway.complete_calls) == 1
    assert gateway.reason_calls == []


def test_tools_mode_dispatches_calls_in_order_and_completes() -> None:
    first_messages = ("assistant tool request",)
    gateway = FakeGateway(
        (
            AgentToolReasoningResult(
                content="",
                tool_calls=(
                    AgentToolCall(id="1", name="first", args={"value": 1}),
                    AgentToolCall(id="2", name="second", args={"value": 2}),
                ),
                messages=first_messages,
            ),
            AgentToolReasoningResult(
                content="tool answer",
                tool_calls=(),
                messages=("final",),
            ),
        )
    )
    registry = FakeToolRegistry()
    runtime = object()

    result = asyncio.run(
        run_agent(
            make_agent(gateway),
            HarnessRequest(
                mode=HarnessMode.TOOLS,
                content="use tools",
                tool_names=("first", "second"),
                tool_registry=registry,
                tool_runtime=runtime,
            ),
        )
    )

    assert result == "tool answer"
    assert registry.calls == [
        ("first", {"value": 1}, runtime),
        ("second", {"value": 2}, runtime),
    ]
    assert gateway.append_calls == [
        (
            first_messages,
            (
                AgentToolResult(tool_call_id="1", content="result:first"),
                AgentToolResult(tool_call_id="2", content="result:second"),
            ),
        )
    ]


@pytest.mark.parametrize(
    ("harness_request", "message"),
    (
        (
            HarnessRequest(
                mode=HarnessMode.PROMPT,
                content="hello",
                tool_names=("first",),
            ),
            "prompt mode does not accept tools",
        ),
        (
            HarnessRequest(mode=HarnessMode.TOOLS, content="hello"),
            "tools mode requires tool_names",
        ),
    ),
)
def test_mode_configuration_is_validated(harness_request, message) -> None:
    with pytest.raises(ValueError, match=message):
        asyncio.run(run_agent(make_agent(FakeGateway()), harness_request))


def test_tools_mode_forces_one_final_answer_at_iteration_limit() -> None:
    gateway = FakeGateway(
        (
            AgentToolReasoningResult(content="", tool_calls=(), messages=("empty-1",)),
            AgentToolReasoningResult(content="", tool_calls=(), messages=("empty-2",)),
        )
    )

    result = asyncio.run(
        run_agent(
            make_agent(gateway),
            HarnessRequest(
                mode=HarnessMode.TOOLS,
                content="finish",
                tool_names=("first",),
                tool_registry=FakeToolRegistry(),
            ),
            ChatOptions(max_iterations=2),
        )
    )

    assert result == "forced answer"
    assert len(gateway.reason_calls) == 2
    assert len(gateway.force_calls) == 1


def test_max_iterations_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_iterations must be greater than zero"):
        asyncio.run(
            run_agent(
                make_agent(FakeGateway()),
                HarnessRequest(mode=HarnessMode.PROMPT, content="hello"),
                ChatOptions(max_iterations=0),
            )
        )


def test_agent_run_phases_are_stable_public_values() -> None:
    assert [phase.value for phase in AgentRunPhase] == [
        "reasoning",
        "tool_running",
        "finalizing",
        "completed",
    ]
