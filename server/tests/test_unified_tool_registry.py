import pytest

from server.app.agent_tool_service import AgentToolDefinition, AgentToolRegistry
from server.domain.agents import AgentConfigError, AgentDefinition, SkillDefinition, ToolAccessDefinition


async def _echo_handler(args, runtime):
    return args


def _definition(name: str = "market_quote", scenes=("mcp", "agent")) -> AgentToolDefinition:
    return AgentToolDefinition(
        name=name,
        display_name="市场行情查询",
        description="查询指定资产行情。",
        input={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
        output={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
        support_scene=scenes,
        handler=_echo_handler,
    )


def test_registry_exposes_structured_public_definitions_and_model_schema() -> None:
    registry = AgentToolRegistry((_definition(),))

    assert registry.public_definitions() == (
        {
            "name": "market_quote",
            "display_name": "市场行情查询",
            "description": "查询指定资产行情。",
            "input": _definition().input,
            "output": _definition().output,
            "support_scene": ["mcp", "agent"],
        },
    )
    assert registry.schemas(("market_quote",))[0]["function"]["parameters"] == _definition().input


def test_registry_rejects_duplicate_names_and_unknown_scenes() -> None:
    with pytest.raises(AgentConfigError, match="duplicate tool name"):
        AgentToolRegistry((_definition(), _definition()))

    with pytest.raises(AgentConfigError, match="support_scene"):
        AgentToolRegistry((_definition(scenes=("browser",)),))


def test_registry_resolves_only_tools_from_bound_skills() -> None:
    registry = AgentToolRegistry((_definition(),))
    agent = AgentDefinition(
        id="assistant",
        name="Assistant",
        system_prompt="Be concise.",
        skill_ids=("common:market",),
    )
    skills = (
        SkillDefinition(
            id="common:market",
            tools=ToolAccessDefinition(allow=("market_quote",)),
        ),
        SkillDefinition(
            id="common:other",
            tools=ToolAccessDefinition(allow=("hidden_tool",)),
        ),
    )

    assert registry.resolve_tools(agent, skills) == ("market_quote",)


def test_tool_access_rejects_legacy_profile_and_deny() -> None:
    with pytest.raises(TypeError):
        ToolAccessDefinition(profile="portfolio")
    with pytest.raises(TypeError):
        ToolAccessDefinition(deny=("market_quote",))
