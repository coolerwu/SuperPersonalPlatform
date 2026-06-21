from collections.abc import Awaitable, Callable

from server.domain.harness.contracts import (
    Agent,
    AgentChatCheckpoint,
    ChatOptions,
    CheckpointEmitter,
    HarnessMode,
    HarnessRequest,
)
from server.domain.harness.prompt import run_prompt_mode
from server.domain.harness.tools import run_tools_mode


ModeRunner = Callable[
    [Agent, HarnessRequest, ChatOptions, CheckpointEmitter],
    Awaitable[str],
]

_MODE_RUNNERS: dict[HarnessMode, ModeRunner] = {
    HarnessMode.PROMPT: run_prompt_mode,
    HarnessMode.TOOLS: run_tools_mode,
}


async def run_agent(
    agent: Agent,
    request: HarnessRequest,
    options: ChatOptions | None = None,
) -> str:
    options = options or ChatOptions()
    if options.max_iterations <= 0:
        raise ValueError("max_iterations must be greater than zero")

    runner = _MODE_RUNNERS.get(request.mode)
    if runner is None:
        raise ValueError(f"unsupported harness mode: {request.mode}")

    async def emit(stage: str, title: str, detail: str = "") -> None:
        if options.on_checkpoint is not None:
            await options.on_checkpoint(
                AgentChatCheckpoint(stage=stage, title=title, detail=detail)
            )

    return await runner(agent, request, options, emit)
