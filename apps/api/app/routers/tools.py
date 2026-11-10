from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.schemas import ToolOut, ToolsOut
from app.services import backtest_tools  # noqa: F401 - registers backtest tools
from app.services import browser_tools  # noqa: F401 - registers browser tools
from app.services.skill_tools import SKILL_TOOL_REGISTRY
from app.services.skills import scan_skills

router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[Depends(require_auth)])

FILESYSTEM_TOOLS = (
    ("ls", "列出工作区目录"),
    ("read_file", "读取工作区文件"),
    ("write_file", "创建或覆盖工作区文件"),
    ("edit_file", "精确替换文件片段"),
    ("delete", "删除工作区文件"),
    ("glob", "按模式查找文件"),
    ("grep", "在工作区搜索文本"),
)

# Static declaration: the SDK runtime does not expose these yet. Session
# search and memory tools return with the tool-layer migration; the page only
# reports the skill registry in the meantime.
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
        )
        for name in sorted(set(registered) | set(declared))
    ]
    return ToolsOut(
        platform_tools=[
            ToolOut(name=name, description=description, registered=True)
            for name, description in PLATFORM_TOOLS
        ],
        filesystem_tools=[
            ToolOut(name=name, description=description, registered=True)
            for name, description in FILESYSTEM_TOOLS
        ],
        skill_tools=skill_tools,
    )
