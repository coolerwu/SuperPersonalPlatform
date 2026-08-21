from dataclasses import dataclass


class ToolDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    name: str
    description: str
    approval_required: bool = False


PLATFORM_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        id="search_context",
        name="Search Context",
        description="Search workspace/context/knowledge/files for relevant knowledge.",
    ),
    ToolDefinition(
        id="write_context",
        name="Write Context",
        description="Write approved knowledge into workspace/context/knowledge/files.",
        approval_required=True,
    ),
    ToolDefinition(
        id="browser_extract",
        name="Browser Extract",
        description="Open a public http/https page with Playwright and extract rendered text and links.",
    ),
)


def get_tool_definition(tool_id: str) -> ToolDefinition:
    normalized = tool_id.strip()
    for definition in PLATFORM_TOOL_DEFINITIONS:
        if definition.id == normalized:
            return definition
    raise ToolDefinitionError(f"unknown platform tool: {normalized}")
