from types import MappingProxyType
from typing import Mapping

from server.domain.harness.contracts import (
    Agent,
    AgentChatCheckpoint,
    AgentModelGateway,
    ChatOptions,
    HarnessMode,
    HarnessModeRunner,
    HarnessRequest,
)
from server.domain.harness.modes.agent import AgentRunner, LLMVerifier
from server.domain.harness.modes.prompt import PromptRunner


class HarnessRuntime:
    def __init__(self, runners: Mapping[HarnessMode, HarnessModeRunner]) -> None:
        self._runners = MappingProxyType(dict(runners))

    def runner(self, mode: HarnessMode) -> HarnessModeRunner:
        runner = self._runners.get(mode)
        if runner is None:
            raise ValueError(f"unsupported harness mode: {mode}")
        return runner


def create_harness_runtime(llm_client: AgentModelGateway) -> HarnessRuntime:
    return HarnessRuntime(
        {
            HarnessMode.PROMPT: PromptRunner(llm_client),
            HarnessMode.AGENT: AgentRunner(llm_client, LLMVerifier(llm_client)),
        }
    )


async def run_agent(
    agent: Agent,
    request: HarnessRequest,
    runtime: HarnessRuntime,
    options: ChatOptions | None = None,
) -> str:
    options = options or ChatOptions()
    if options.max_iterations <= 0:
        raise ValueError("max_iterations must be greater than zero")

    async def emit(stage: str, title: str, detail: str = "") -> None:
        if options.on_checkpoint is not None:
            await options.on_checkpoint(
                AgentChatCheckpoint(stage=stage, title=title, detail=detail)
            )

    return await runtime.runner(request.mode).run(agent, request, options, emit)
