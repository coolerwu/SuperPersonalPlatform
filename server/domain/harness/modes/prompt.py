from server.domain.harness.contracts import (
    AgentModelRunner,
    CheckpointEmitter,
    HarnessRequest,
)


class PromptRunner:
    def __init__(self, model_runner: AgentModelRunner) -> None:
        self._model_runner = model_runner

    async def run(
        self,
        request: HarnessRequest,
        emit: CheckpointEmitter,
    ) -> str:
        if (
            request.tool_names
            or request.tool_registry is not None
            or request.tool_runtime is not None
        ):
            raise ValueError("prompt mode does not accept tools")
        agent = request.agent
        await emit("answer", "生成最终回复", "")
        message = await self._model_runner.complete(
            agent.definition.system_prompt,
            request.content,
            request.images,
        )
        await emit("answer", "最终回复已生成", "")
        return message
