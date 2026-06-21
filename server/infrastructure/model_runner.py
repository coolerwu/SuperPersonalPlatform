from typing import Any

from server.domain.harness import (
    AgentToolCall,
    AgentToolCallingUnsupportedError,
    AgentToolReasoningResult,
    AgentToolResult,
    ChatImage,
)
from server.app.agent_tool_service import DEFAULT_AGENT_TOOL_REGISTRY
from server.domain.agents import ModelDefinition


class ModelRunner:
    def __init__(self, model: ModelDefinition) -> None:
        self._model = model

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...] = (),
    ) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        chat_model = self._chat_model()
        human_content: str | list[dict[str, object]] = self._human_content(user_message, images)
        response = await chat_model.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]
        )
        return str(response.content)

    async def complete_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tool_names: tuple[str, ...],
        skill_tools: Any,
        images: tuple[ChatImage, ...] = (),
        max_iterations: int = 60,
    ) -> str:
        messages: tuple[Any, ...] = ()
        for _ in range(max_iterations):
            reasoning = await self.reason_with_tools(
                system_prompt,
                user_message,
                tool_names,
                messages,
                images,
            )
            messages = reasoning.messages
            if not reasoning.tool_calls:
                return reasoning.content
            tool_results: list[AgentToolResult] = []
            for tool_call in reasoning.tool_calls:
                tool_results.append(
                    AgentToolResult(
                        tool_call_id=tool_call.id,
                        content=await self._run_tool(skill_tools, tool_call),
                    )
                )
            messages = self.append_tool_results(messages, tuple(tool_results))

        return await self.force_tool_final(messages)

    async def reason_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tool_names: tuple[str, ...],
        messages: tuple[Any, ...],
        images: tuple[ChatImage, ...] = (),
    ) -> AgentToolReasoningResult:
        from langchain_core.messages import HumanMessage, SystemMessage

        chat_model = self._chat_model()
        tools = self._tool_schemas(tool_names)
        if not tools:
            return AgentToolReasoningResult(
                content=await self.complete(system_prompt, user_message, images),
                tool_calls=(),
                messages=messages,
            )

        try:
            bound_model = chat_model.bind_tools(tools)
        except Exception as exc:
            raise AgentToolCallingUnsupportedError(
                "当前模型不支持 LangChain tools"
            ) from exc

        next_messages = list(messages)
        if not next_messages:
            next_messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=self._human_content(user_message, images)),
            ]
        try:
            response = await bound_model.ainvoke(next_messages)
        except Exception as exc:
            raise AgentToolCallingUnsupportedError(
                "当前模型不支持 LangChain tools 或工具调用失败"
            ) from exc
        next_messages.append(response)
        tool_calls = tuple(self._tool_call(tool_call) for tool_call in getattr(response, "tool_calls", None) or [])
        return AgentToolReasoningResult(
            content=str(response.content),
            tool_calls=tool_calls,
            messages=tuple(next_messages),
        )

    def append_tool_results(
        self,
        messages: tuple[Any, ...],
        tool_results: tuple[AgentToolResult, ...],
    ) -> tuple[Any, ...]:
        from langchain_core.messages import ToolMessage

        next_messages = list(messages)
        for result in tool_results:
            next_messages.append(
                ToolMessage(
                    content=result.content,
                    tool_call_id=result.tool_call_id,
                )
            )
        return tuple(next_messages)

    async def force_tool_final(
        self,
        messages: tuple[Any, ...],
    ) -> str:
        from langchain_core.messages import HumanMessage

        chat_model = self._chat_model()
        final_messages = list(messages)
        final_messages.append(
            HumanMessage(
                content=(
                    "工具调用已达到 60 轮上限。请基于已有工具结果直接给出最终回答，"
                    "不要继续调用工具。"
                )
            )
        )
        response = await chat_model.ainvoke(final_messages)
        return str(response.content)

    def _human_content(
        self,
        user_message: str,
        images: tuple[ChatImage, ...],
    ) -> str | list[dict[str, object]]:
        if not images:
            return user_message
        human_content: list[dict[str, object]] = []
        if user_message:
            human_content.append({"type": "text", "text": user_message})
        for image in images:
            human_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.mime_type};base64,{image.data}",
                    },
                }
            )
        return human_content

    def _chat_model(self):
        model = self._model
        if model.provider == "anthropic":
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

    def _tool_schemas(self, tool_names: tuple[str, ...]) -> list[dict[str, object]]:
        return DEFAULT_AGENT_TOOL_REGISTRY.schemas(tool_names)

    def _tool_call(self, raw_tool_call: dict[str, Any]) -> AgentToolCall:
        args = raw_tool_call.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        return AgentToolCall(
            id=str(raw_tool_call.get("id") or ""),
            name=str(raw_tool_call.get("name") or ""),
            args=args,
        )

    async def _run_tool(self, skill_tools: Any, tool_call: AgentToolCall) -> str:
        name = tool_call.name
        args = tool_call.args
        if name == "list_skill":
            return await skill_tools.list_skill()
        if name == "read_skill":
            return await skill_tools.read_skill(str(args.get("id") or ""))
        return f"Unsupported tool: {name}"
