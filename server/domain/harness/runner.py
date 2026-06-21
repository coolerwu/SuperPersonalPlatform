from server.domain.agents import HarnessMode, ModelDefinition
from server.domain.harness.contracts import (
    AgentChatCheckpoint,
    AgentModelRunner,
    HarnessRequest,
)
from server.domain.harness.modes.agent import AgentRunner, LLMVerifier
from server.domain.harness.modes.prompt import PromptRunner


def create_model_runner(model: ModelDefinition) -> AgentModelRunner:
    from server.infrastructure.model_runner import ModelRunner

    return ModelRunner(model)


async def run_agent(
    request: HarnessRequest,
) -> str:
    if request.max_iterations <= 0:
        raise ValueError("max_iterations must be greater than zero")

    async def emit(stage: str, title: str, detail: str = "") -> None:
        if request.on_checkpoint is not None:
            await request.on_checkpoint(
                AgentChatCheckpoint(stage=stage, title=title, detail=detail)
            )

    agent = request.agent
    model_runner = create_model_runner(agent.model)
    if agent.model.mode is HarnessMode.PROMPT:
        runner = PromptRunner(model_runner)
    elif agent.model.mode is HarnessMode.AGENT:
        runner = AgentRunner(model_runner, LLMVerifier(model_runner))
    else:
        raise ValueError(f"unsupported harness mode: {agent.model.mode}")
    return await runner.run(request, emit)
