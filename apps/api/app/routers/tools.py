from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth import require_auth
from app.schemas import (
    McpServerOut,
    RuntimePluginErrorOut,
    RuntimePluginOut,
    ToolOut,
    ToolsOut,
)
from app.services import allocation_tools  # noqa: F401 - registers allocation tools
from app.services import backtest_tools  # noqa: F401 - registers backtest tools
from app.services import chan_tools  # noqa: F401 - registers Chan tools
from app.services import trade_journal_tools  # noqa: F401 - registers journal tools
from app.services.platform_mcp import SERVER_NAME as PLATFORM_SERVER
from app.services.skill_tools import SKILL_TOOL_REGISTRY

router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[Depends(require_auth)])

NOT_LOADED_NOTE = (
    "还没有运行时数据。先给这个 Agent 发一条消息，完成一轮对话后，"
    "这里会显示 Claude Code 实际加载的工具、MCP、技能和插件。"
)
LOADED_NOTE = "数据来自该 Agent 最近一轮对话的运行时 init frame。"


def _platform_descriptions() -> dict[str, str]:
    return {spec.name: spec.description for spec in SKILL_TOOL_REGISTRY.specs()}


def _tool_out(name: str, descriptions: dict[str, str]) -> ToolOut:
    if not name.startswith("mcp__"):
        return ToolOut(name=name, raw_name=name, source="claude-code")
    server, _, tool = name[len("mcp__") :].partition("__")
    description = descriptions.get(tool, "") if server == PLATFORM_SERVER else ""
    return ToolOut(
        name=tool or name,
        raw_name=name,
        server=server,
        source="mcp",
        description=description,
    )


@router.get("", response_model=ToolsOut)
async def list_tools(request: Request, agent_id: str = "") -> ToolsOut:
    """Tools the Agent actually has, straight from its latest init frame."""
    runner = getattr(request.app.state, "runner", None)
    lookup = getattr(runner, "runtime_snapshot", None)
    snapshot = lookup(agent_id) if lookup is not None and agent_id else None
    if not snapshot:
        return ToolsOut(loaded=False, agent_id=agent_id, note=NOT_LOADED_NOTE)

    descriptions = _platform_descriptions()
    claude_tools: list[ToolOut] = []
    mcp_tools: list[ToolOut] = []
    for name in snapshot.get("tools", []):
        tool = _tool_out(name, descriptions)
        (mcp_tools if tool.source == "mcp" else claude_tools).append(tool)
    claude_tools.sort(key=lambda item: item.name.lower())
    mcp_tools.sort(key=lambda item: (item.server.lower(), item.name.lower()))
    return ToolsOut(
        loaded=True,
        agent_id=agent_id,
        claude_code_version=snapshot.get("claude_code_version", ""),
        model=snapshot.get("model", ""),
        permission_mode=snapshot.get("permission_mode", ""),
        claude_tools=claude_tools,
        mcp_tools=mcp_tools,
        mcp_servers=sorted(
            (McpServerOut(**row) for row in snapshot.get("mcp_servers", [])),
            key=lambda server: server.name.lower(),
        ),
        skills=sorted(snapshot.get("skills", [])),
        plugins=sorted(
            (RuntimePluginOut(**row) for row in snapshot.get("plugins", [])),
            key=lambda plugin: plugin.name.lower(),
        ),
        plugin_errors=[
            RuntimePluginErrorOut(**row)
            for row in snapshot.get("plugin_errors", [])
        ],
        note=LOADED_NOTE,
    )
