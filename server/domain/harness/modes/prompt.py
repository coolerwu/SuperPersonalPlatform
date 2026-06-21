from server.domain.harness.contracts import (
    Agent,
    AgentModelGateway,
    ChatOptions,
    CheckpointEmitter,
    HarnessRequest,
)


class PromptRunner:
    def __init__(self, llm_client: AgentModelGateway) -> None:
        self._llm_client = llm_client

    async def run(
        self,
        agent: Agent,
        request: HarnessRequest,
        _options: ChatOptions,
        emit: CheckpointEmitter,
    ) -> str:
        if (
            request.tool_names
            or request.tool_registry is not None
            or request.tool_runtime is not None
        ):
            raise ValueError("prompt mode does not accept tools")
        await emit("answer", "生成最终回复", "")
        message = await self._llm_client.complete(
            agent.model,
            agent.definition.system_prompt,
            request.content,
            request.images,
        )
        await emit("answer", "最终回复已生成", "")
        return message
