import asyncio

import pytest

from server.domain.harness import AgentToolCallingUnsupportedError
from server.domain.agents import ModelDefinition
from server.infrastructure.model_runner import ModelRunner


class FakeSkillTools:
    async def list_skill(self) -> str:
        return '{"skills":[{"id":"common:writing"}]}'

    async def read_skill(self, id: str) -> str:
        return f'{{"id":"{id}","content":"skill body"}}'


class FakeResponse:
    def __init__(self, content="", tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class FakeBoundModel:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if not any(message.__class__.__name__ == "ToolMessage" for message in messages):
            return FakeResponse(
                tool_calls=[
                    {"name": "read_skill", "args": {"id": "common:writing"}, "id": "call-1"}
                ]
            )
        return FakeResponse(content="final answer")


class FakeChatOpenAI:
    instances = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.bound_tools = None
        self.bound_model = FakeBoundModel()
        self.final_calls = []
        FakeChatOpenAI.instances.append(self)

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self.bound_model

    async def ainvoke(self, messages):
        self.final_calls.append(messages)
        return FakeResponse(content="forced final")


def model() -> ModelDefinition:
    return ModelDefinition(
        id="fast",
        name="Fast",
        base_url="https://llm.example.test/v1",
        api_key="secret",
        model="fast-chat",
    )


def test_model_runner_binds_tools_and_returns_final_answer(monkeypatch) -> None:
    import langchain_openai

    FakeChatOpenAI.instances = []
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)
    adapter = ModelRunner(model())

    result = asyncio.run(
        adapter.complete_with_tools(
            "system",
            "user",
            ("list_skill", "read_skill"),
            FakeSkillTools(),
        )
    )

    assert result == "final answer"
    instance = FakeChatOpenAI.instances[-1]
    assert [tool["function"]["name"] for tool in instance.bound_tools] == [
        "list_skill",
        "read_skill",
    ]
    assert len(FakeChatOpenAI.instances) == 2


def test_model_runner_forces_final_answer_after_max_iterations(monkeypatch) -> None:
    import langchain_openai

    class LoopingBoundModel:
        async def ainvoke(self, messages):
            return FakeResponse(
                tool_calls=[
                    {"name": "list_skill", "args": {}, "id": f"call-{len(messages)}"}
                ]
            )

    class LoopingChatOpenAI(FakeChatOpenAI):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.bound_model = LoopingBoundModel()

    FakeChatOpenAI.instances = []
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", LoopingChatOpenAI)
    adapter = ModelRunner(model())

    result = asyncio.run(
        adapter.complete_with_tools(
            "system",
            "user",
            ("list_skill",),
            FakeSkillTools(),
            max_iterations=1,
        )
    )

    assert result == "forced final"
    final_instance = next(instance for instance in FakeChatOpenAI.instances if instance.final_calls)
    assert "工具调用已达到 60 轮上限" in str(final_instance.final_calls[0][-1].content)


def test_model_runner_reports_tool_calling_unsupported(monkeypatch) -> None:
    import langchain_openai

    class UnsupportedChatOpenAI(FakeChatOpenAI):
        def bind_tools(self, tools):
            raise RuntimeError("unsupported")

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", UnsupportedChatOpenAI)
    adapter = ModelRunner(model())

    with pytest.raises(AgentToolCallingUnsupportedError):
        asyncio.run(
            adapter.complete_with_tools(
                "system",
                "user",
                ("list_skill",),
                FakeSkillTools(),
            )
        )
