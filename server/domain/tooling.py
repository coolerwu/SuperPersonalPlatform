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
        description="Search local context files and synced readable WebDAV permission paths for relevant knowledge.",
    ),
    ToolDefinition(
        id="search_session",
        name="Search Session",
        description="Search the current conversation session history by keyword.",
    ),
    ToolDefinition(
        id="arxiv",
        name="arXiv",
        description="Search arXiv papers with a built-in 3 second request interval.",
    ),
    ToolDefinition(
        id="yahoo_finance_news",
        name="Yahoo Finance News",
        description="Fetch lightweight Yahoo Finance news for a public ticker.",
    ),
    ToolDefinition(
        id="write_context",
        name="Write Context",
        description="Write approved knowledge into local context files or writable non-protected WebDAV permission paths.",
        approval_required=True,
    ),
    ToolDefinition(
        id="browser_extract",
        name="Browser Extract",
        description="Open a public http/https page with Playwright and extract rendered text and links.",
    ),
    ToolDefinition(
        id="schedule",
        name="Schedule",
        description="Create, list, get, update, and delete this Agent's own scheduled tasks with current-channel delivery.",
    ),
)


def get_tool_definition(tool_id: str) -> ToolDefinition:
    normalized = tool_id.strip()
    for definition in PLATFORM_TOOL_DEFINITIONS:
        if definition.id == normalized:
            return definition
    raise ToolDefinitionError(f"unknown platform tool: {normalized}")
