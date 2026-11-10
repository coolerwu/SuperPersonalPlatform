from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.schemas import ToolOut, ToolsOut
from app.services import allocation_tools  # noqa: F401 - registers allocation tools
from app.services import backtest_tools  # noqa: F401 - registers backtest tools
from app.services import browser_tools  # noqa: F401 - registers browser tools
from app.services import chan_tools  # noqa: F401 - registers Chan tools
from app.services import trade_journal_tools  # noqa: F401 - registers journal tools
from app.services.skill_tools import SKILL_TOOL_REGISTRY
from app.services.skills import scan_skills

router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[Depends(require_auth)])

# Claude Code's built-in tools. The SDK runtime exposes them directly; they do
# not come from the skill registry in this version.
FILESYSTEM_TOOLS = (
    ("Read", "读取工作区文件"),
    ("Write", "创建或覆盖工作区文件"),
    ("Edit", "精确替换文件片段"),
    ("Bash", "在宿主机执行命令；没有沙箱，工作目录是当前 Agent 工作区"),
    ("Glob", "按模式查找文件"),
    ("Grep", "在工作区搜索文本"),
)

# Static declaration: the SDK runtime does not expose platform tools yet.
PLATFORM_TOOLS = (
    (
        "skill_save",
        "把可复用的流程保存为技能；创建后自动加入当前 Agent 的启用列表，"
        "下一次对话生效。",
    ),
)


@router.get("", response_model=ToolsOut)
async def list_tools() -> ToolsOut:
    declared: dict[str, set[str]] = {}
    for skill in scan_skills().skills:
        for tool_name in skill.include_tools:
            declared.setdefault(tool_name, set()).add(skill.name)

    registered = {spec.name: spec for spec in SKILL_TOOL_REGISTRY.specs()}
    skill_tools = [
        ToolOut(
            name=name,
            description=registered[name].description if name in registered else "",
            registered=name in registered,
            skills=sorted(declared.get(name, ())),
            source="skill",
            # The tool-layer migration has not connected registry tools to the
            # SDK runtime yet.
            wired=False,
        )
        for name in sorted(set(registered) | set(declared))
    ]
    return ToolsOut(
        platform_tools=[
            ToolOut(
                name=name,
                description=description,
                registered=False,
                source="platform",
                wired=False,
            )
            for name, description in PLATFORM_TOOLS
        ],
        filesystem_tools=[
            ToolOut(
                name=name,
                description=description,
                registered=True,
                source="claude-code",
                wired=True,
            )
            for name, description in FILESYSTEM_TOOLS
        ],
        skill_tools=skill_tools,
    )
