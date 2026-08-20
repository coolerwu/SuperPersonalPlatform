from dataclasses import dataclass
from typing import Any

from server.domain.agent_config import ModelDefinition, ModelProvider


@dataclass(frozen=True)
class RuntimeMessage:
    role: str
    content: str


@dataclass(frozen=True)
class DeepAgentRuntimeOptions:
    max_iterations: int = 60
    name: str = ""
    debug: bool = False
    interrupt_on: tuple[str, ...] = ()


class DeepAgentRuntime:
    def __init__(self, model: ModelDefinition) -> None:
        self._model = model

    async def run(
        self,
        *,
        instructions: str,
        messages: tuple[RuntimeMessage, ...],
        options: DeepAgentRuntimeOptions,
    ) -> str:
        if not messages:
            raise ValueError("messages are required")
        try:
            from deepagents import create_deep_agent
            from langchain_core.messages import AIMessage, HumanMessage
        except Exception as exc:
            raise RuntimeError("DeepAgent runtime requires the deepagents package") from exc

        create_kwargs: dict[str, Any] = {
            "tools": [],
            "model": self._chat_model(),
            "instructions": instructions,
        }
        name = options.name.strip()
        if name:
            create_kwargs["name"] = name
        if options.debug:
            create_kwargs["debug"] = True
        interrupt_on = _normalize_interrupt_on(options.interrupt_on)
        if interrupt_on:
            create_kwargs["interrupt_on"] = interrupt_on
        try:
            agent = create_deep_agent(**create_kwargs)
        except TypeError:
            create_kwargs["system_prompt"] = create_kwargs.pop("instructions")
            agent = create_deep_agent(**create_kwargs)

        input_messages = _to_langchain_messages(messages, HumanMessage, AIMessage)
        result = await agent.ainvoke(
            {"messages": input_messages},
            config={"recursion_limit": options.max_iterations},
        )
        return self._extract_content(result)

    def _chat_model(self):
        model = self._model
        if model.provider is ModelProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic

            kwargs: dict[str, object] = {
                "api_key": model.api_key,
                "model": model.model,
                "temperature": model.temperature if model.temperature is not None else 0.7,
            }
            if model.base_url.strip():
                kwargs["base_url"] = model.base_url
            return ChatAnthropic(**kwargs)

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=model.api_key,
            base_url=model.base_url,
            model=model.model,
            temperature=model.temperature if model.temperature is not None else 0.7,
        )

    def _extract_content(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            messages = result.get("messages")
            if isinstance(messages, list) and messages:
                content = getattr(messages[-1], "content", None)
                if content is not None:
                    return str(content)
            for key in ("output", "content", "answer", "response"):
                if key in result:
                    return str(result[key])
        return str(result)


def _normalize_interrupt_on(value: Any) -> dict[str, bool] | None:
    if isinstance(value, dict):
        return {str(key): bool(item) for key, item in value.items() if str(key).strip()}
    if isinstance(value, list):
        return {str(item).strip(): True for item in value if str(item).strip()}
    return None


def _to_langchain_messages(messages: tuple[RuntimeMessage, ...], human_cls: Any, ai_cls: Any) -> list[Any]:
    result: list[Any] = []
    for message in messages:
        content = message.content.strip()
        if not content:
            continue
        role = message.role.lower()
        if role in {"assistant", "ai"}:
            result.append(ai_cls(content=content))
        elif role in {"user", "human"}:
            result.append(human_cls(content=content))
    return result
