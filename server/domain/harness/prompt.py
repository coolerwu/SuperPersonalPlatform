from server.domain.harness.contracts import (
    Agent,
    ChatOptions,
    CheckpointEmitter,
    HarnessRequest,
)


async def run_prompt_mode(
    agent: Agent,
    request: HarnessRequest,
    _options: ChatOptions,
    emit: CheckpointEmitter,
) -> str:
    _validate_prompt_request(request)
    await emit("answer", "生成最终回复", "")
    message = await agent.llm_client.complete(
        agent.model,
        agent.definition.system_prompt,
        request.content,
        request.images,
    )
    await emit("answer", "最终回复已生成", "")
    return message


def _validate_prompt_request(request: HarnessRequest) -> None:
    if (
        request.tool_names
        or request.tool_registry is not None
        or request.tool_runtime is not None
    ):
        raise ValueError("prompt mode does not accept tools")
