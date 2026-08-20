from typing import Any

from server.domain.agents import ModelDefinition, ModelProvider


class ModelRunner:
    def __init__(self, model: ModelDefinition) -> None:
        self._model = model

    async def run_deep_agent(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_iterations: int = 60,
        deepagent_options: dict[str, Any] | None = None,
    ) -> str:
        try:
            from deepagents import create_deep_agent
            from langchain_core.messages import HumanMessage
        except Exception as exc:
            raise RuntimeError("DeepAgent runtime requires the deepagents package") from exc

        options = deepagent_options if isinstance(deepagent_options, dict) else {}
        iterations = int(options.get("max_iterations") or max_iterations)
        create_kwargs: dict[str, Any] = {
            "tools": [],
            "model": self._chat_model(),
            "instructions": system_prompt,
        }
        name = str(options.get("name") or "").strip()
        if name:
            create_kwargs["name"] = name
        if bool(options.get("debug", False)):
            create_kwargs["debug"] = True
        interrupt_on = _normalize_interrupt_on(options.get("interrupt_on"))
        if interrupt_on:
            create_kwargs["interrupt_on"] = interrupt_on
        try:
            agent = create_deep_agent(**create_kwargs)
        except TypeError:
            create_kwargs["system_prompt"] = create_kwargs.pop("instructions")
            agent = create_deep_agent(**create_kwargs)

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_message)]},
            config={"recursion_limit": iterations},
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
