from server.app.agent_tool_service import AgentToolDefinition, AgentToolRegistry
from server.app.agent_capability_search_service import AgentCapabilitySearchService
from server.domain.agents import AgentDefinition, AgentPlatformDefinition, ModelDefinition, SkillDefinition, ToolAccessDefinition


async def _noop_handler(args, runtime):
    return "{}"


def _tool(name: str, display_name: str, description: str) -> AgentToolDefinition:
    return AgentToolDefinition(
        name=name,
        display_name=display_name,
        description=description,
        input={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_noop_handler,
    )


def _platform() -> AgentPlatformDefinition:
    return AgentPlatformDefinition(
        models=(
            ModelDefinition(
                id="agent-model",
                name="Agent Model",
                base_url="https://llm.example.test/v1",
                api_key="secret",
                model="agent-chat",
            ),
        ),
        default_model_id="agent-model",
        agents=(
            AgentDefinition(
                id="designer",
                name="Designer",
                system_prompt="Review product UI.",
                model_id="agent-model",
                skill_ids=("common:design-audit",),
            ),
        ),
        skill_definitions=(
            SkillDefinition(
                id="common:design-audit",
                name="产品体验审查",
                tools=ToolAccessDefinition(allow=("capture_page",)),
            ),
            SkillDefinition(id="common:writing", name="写作润色"),
        ),
    )


def test_capability_search_matches_registered_metadata_and_marks_agent_scope() -> None:
    registry = AgentToolRegistry(
        (
            _tool("capture_page", "页面截图", "捕获页面截图并读取浏览器控制台，用于交互和视觉审查。"),
            _tool("update_document", "更新文档", "修改项目文档内容。"),
        )
    )
    service = AgentCapabilitySearchService(registry)

    results = service.search(
        platform=_platform(),
        query="页面审查",
        types=("skill", "tool"),
        agent_id="designer",
    )

    assert [item.id for item in results[:2]] == ["common:design-audit", "capture_page"]
    design_skill = results[0]
    assert design_skill.type == "skill"
    assert design_skill.discoverable is True
    assert design_skill.loadable is True
    assert design_skill.callable is False
    assert "审查" in design_skill.matched_terms

    capture_tool = results[1]
    assert capture_tool.type == "tool"
    assert capture_tool.discoverable is True
    assert capture_tool.loadable is True
    assert capture_tool.callable is True
    assert capture_tool.required_skills == ["common:design-audit"]


def test_capability_search_does_not_invent_unregistered_production_capabilities() -> None:
    registry = AgentToolRegistry(
        (
            _tool("capture_page", "页面截图", "捕获页面截图。"),
            _tool("service_status", "状态检查", "Inspect backend service status."),
        )
    )
    service = AgentCapabilitySearchService(registry)

    results = service.search(
        platform=_platform(),
        query="重启服务",
        types=("tool",),
        agent_id="designer",
    )

    assert results == ()
