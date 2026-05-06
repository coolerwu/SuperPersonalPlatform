from server.app.agent_chat_service import AgentChatModelGateway, ChatImage
from server.domain.agents import ModelDefinition


class LangChainOpenAICompatibleAdapter(AgentChatModelGateway):
    async def complete(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...] = (),
    ) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        chat_model = ChatOpenAI(
            api_key=model.api_key,
            base_url=model.base_url,
            model=model.model,
            temperature=model.temperature if model.temperature is not None else 0.7,
        )
        human_content: str | list[dict[str, object]]
        if images:
            human_content = []
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
        else:
            human_content = user_message

        response = await chat_model.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]
        )
        return str(response.content)
