from dataclasses import dataclass


class ToolDefinitionError(ValueError):
    pass


SYSTEM_PROMPT_TOOL_ID = "update_system_prompt"


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    name: str
    description: str
    approval_required: bool = False
    always_on: bool = False


PLATFORM_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        id="send_attachment", name="Send Attachment",
        description="Queue an existing image or file for delivery to the current WeChat conversation.",
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
        id="browser_extract",
        name="Browser Extract",
        description="Open a public http/https page with Playwright and extract rendered text and links.",
    ),
    ToolDefinition(
        id="schedule",
        name="Schedule",
        description="Create, list, get, update, and delete this Agent's own scheduled tasks with current-channel delivery.",
    ),
    ToolDefinition(
        id="execute_code",
        name="Execute Code",
        description="Run short Python or shell code in a Docker + gVisor sandbox with no network and artifact collection.",
    ),
    ToolDefinition(
        id=SYSTEM_PROMPT_TOOL_ID,
        name="Update System Prompt",
        description=(
            "Replace this Agent's own system prompt in config.yaml after human approval. "
            "System capability, not an authorization choice."
        ),
        approval_required=True,
        always_on=True,
    ),
)


SYSTEM_APPROVAL_TOOL_IDS: tuple[str, ...] = tuple(
    definition.id for definition in PLATFORM_TOOL_DEFINITIONS
    if definition.approval_required and not definition.always_on
)

ALWAYS_ON_APPROVAL_TOOL_IDS: tuple[str, ...] = tuple(
    definition.id for definition in PLATFORM_TOOL_DEFINITIONS
    if definition.always_on and definition.approval_required
)


def get_tool_definition(tool_id: str) -> ToolDefinition:
    normalized = tool_id.strip()
    for definition in PLATFORM_TOOL_DEFINITIONS:
        if definition.id == normalized:
            return definition
    raise ToolDefinitionError(f"unknown platform tool: {normalized}")
