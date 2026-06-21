import asyncio
import importlib
from dataclasses import fields
from unittest.mock import patch

import pytest

from server.domain.agents import AgentDefinition, ModelDefinition
from server.domain.harness import (
    Agent,
    AgentRunFailedError,
    AgentRunPhase,
    AgentToolCall,
    AgentToolReasoningResult,
    AgentToolResult,
    HarnessMode,
    HarnessRequest,
    run_agent,
)


GOAL_JSON = (
    '{"goal":"use the tools","completion_criteria":["answer the request"],'
    '"output_format":"plain text","required_evidence":["first"]}'
)
VERIFY_PASS_JSON = '{"passed":true,"blocked":false,"feedback":""}'


class FakeGateway:
    def __init__(self, completions=(), reasoning_results=()) -> None:
        self.completions = list(completions)
        self.reasoning_results = list(reasoning_results)
        self.complete_calls = []
        self.reason_calls = []
        self.append_calls = []

    async def complete(self, system_prompt, content, images):
        self.complete_calls.append((system_prompt, content, images))
        return self.completions.pop(0)

    async def reason_with_tools(
        self,
        system_prompt,
        content,
        tool_names,
        messages,
        images,
    ):
        self.reason_calls.append(
            (system_prompt, content, tool_names, messages, images)
        )
        return self.reasoning_results.pop(0)

    def append_tool_results(self, messages, results):
        self.append_calls.append((messages, results))
        return (*messages, *results)

    async def force_tool_final(self, messages):
        raise AssertionError("AGENT mode must not bypass VERIFY with force_tool_final")


class FakeToolRegistry:
    def __init__(self) -> None:
        self.calls = []

    async def dispatch(self, name, args, runtime):
        self.calls.append((name, args, runtime))
        return f" evidence from {name} "


def make_agent(mode: HarnessMode = HarnessMode.PROMPT) -> Agent:
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
            mode=mode,
        ),
    )


def execute(request, gateway):
    with patch(
        "server.domain.harness.runner.create_model_runner",
        return_value=gateway,
    ):
        return asyncio.run(run_agent(request))


def test_harness_modes_have_separate_modules() -> None:
    runner_module = importlib.import_module("server.domain.harness.runner")
    prompt_module = importlib.import_module("server.domain.harness.modes.prompt")
    agent_module = importlib.import_module("server.domain.harness.modes.agent")

    assert callable(runner_module.run_agent)
    assert prompt_module.PromptRunner
    assert agent_module.AgentRunner
    assert agent_module.AgentRunPhase is AgentRunPhase


def test_agent_is_pure_configuration_without_model_gateway() -> None:
    assert [field.name for field in fields(Agent)] == ["definition", "model"]


def test_prompt_mode_uses_only_prompt_runner() -> None:
    gateway = FakeGateway(completions=("prompt answer",))
    request = HarnessRequest(agent=make_agent(), content="hello")
    with patch(
        "server.domain.harness.runner.create_model_runner",
        return_value=gateway,
    ):
        result = asyncio.run(run_agent(request))

    assert result == "prompt answer"
    assert len(gateway.complete_calls) == 1
    assert gateway.reason_calls == []


def test_agent_mode_runs_goal_tools_observe_verify_and_finalize() -> None:
    first_messages = ("assistant tool request",)
    gateway = FakeGateway(
        completions=(GOAL_JSON, VERIFY_PASS_JSON, "verified final answer"),
        reasoning_results=(
            AgentToolReasoningResult(
                content="",
                tool_calls=(
                    AgentToolCall(id="1", name="first", args={"value": 1}),
                ),
                messages=first_messages,
            ),
            AgentToolReasoningResult(
                content="candidate answer",
                tool_calls=(),
                messages=("candidate",),
            ),
        ),
    )
    registry = FakeToolRegistry()
    runtime_value = object()
    checkpoints = []

    async def checkpoint(event):
        checkpoints.append(event.stage)

    result = execute(
        HarnessRequest(
            agent=make_agent(HarnessMode.AGENT),
            content="use tools",
            tool_names=("first",),
            tool_registry=registry,
            tool_runtime=runtime_value,
            on_checkpoint=checkpoint,
        ),
        gateway,
    )

    assert result == "verified final answer"
    assert registry.calls == [("first", {"value": 1}, runtime_value)]
    assert gateway.append_calls == [
        (
            first_messages,
            (AgentToolResult(tool_call_id="1", content="evidence from first"),),
        )
    ]
    assert checkpoints == [
        "goal",
        "reason",
        "reason",
        "act",
        "act",
        "observe",
        "reason",
        "verify",
        "finalize",
        "completed",
    ]
    assert len(gateway.complete_calls) == 3


def test_failed_verification_returns_to_reason() -> None:
    gateway = FakeGateway(
        completions=(
            GOAL_JSON.replace('["first"]', "[]"),
            '{"passed":false,"blocked":false,"feedback":"missing detail"}',
            VERIFY_PASS_JSON,
            "second candidate final",
        ),
        reasoning_results=(
            AgentToolReasoningResult(
                content="first candidate", tool_calls=(), messages=("first",)
            ),
            AgentToolReasoningResult(
                content="second candidate", tool_calls=(), messages=("second",)
            ),
        ),
    )

    result = execute(
        HarnessRequest(
            agent=make_agent(HarnessMode.AGENT),
            content="answer carefully",
            tool_names=("first",),
            tool_registry=FakeToolRegistry(),
            max_iterations=2,
        ),
        gateway,
    )

    assert result == "second candidate final"
    assert len(gateway.reason_calls) == 2


def test_agent_mode_fails_instead_of_degrading_at_iteration_limit() -> None:
    gateway = FakeGateway(
        completions=(
            GOAL_JSON.replace('["first"]', "[]"),
            '{"passed":false,"blocked":false,"feedback":"not complete"}',
        ),
        reasoning_results=(
            AgentToolReasoningResult(
                content="weak candidate", tool_calls=(), messages=("weak",)
            ),
        ),
    )
    checkpoints = []

    async def checkpoint(event):
        checkpoints.append(event.stage)

    with pytest.raises(AgentRunFailedError, match="not complete"):
        execute(
            HarnessRequest(
                agent=make_agent(HarnessMode.AGENT),
                content="finish",
                tool_names=("first",),
                tool_registry=FakeToolRegistry(),
                max_iterations=1,
                on_checkpoint=checkpoint,
            ),
            gateway,
        )
    assert checkpoints[-1] == "failed"


def test_prompt_mode_rejects_tool_context() -> None:
    with pytest.raises(ValueError, match="prompt mode does not accept tools"):
        execute(
            HarnessRequest(
                agent=make_agent(), content="hello", tool_names=("first",)
            ),
            FakeGateway(),
        )


def test_agent_mode_without_tools_can_complete() -> None:
    gateway = FakeGateway(
        completions=(
            GOAL_JSON.replace('["first"]', "[]"),
            VERIFY_PASS_JSON,
            "final answer",
        ),
        reasoning_results=(
            AgentToolReasoningResult(
                content="candidate answer", tool_calls=(), messages=("candidate",)
            ),
        ),
    )

    result = execute(
        HarnessRequest(
            agent=make_agent(HarnessMode.AGENT),
            content="answer without tools",
        ),
        gateway,
    )

    assert result == "final answer"
    assert gateway.reason_calls[0][2] == ()


def test_max_iterations_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_iterations must be greater than zero"):
        execute(
            HarnessRequest(agent=make_agent(), content="hello", max_iterations=0),
            FakeGateway(),
        )


def test_agent_run_phases_are_stable_public_values() -> None:
    assert [phase.value for phase in AgentRunPhase] == [
        "goal",
        "reason",
        "act",
        "observe",
        "verify",
        "finalize",
        "completed",
        "failed",
        "blocked",
        "cancelled",
    ]
