from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.schemas import ToolOut, ToolsOut
from app.services import allocation_tools  # noqa: F401 - registers allocation tools
from app.services import backtest_tools  # noqa: F401 - registers backtest tools
from app.services import browser_tools  # noqa: F401 - registers browser tools
from app.services import chan_tools  # noqa: F401 - registers Chan tools
from app.services import trade_journal_tools  # noqa: F401 - registers journal tools
from app.services.platform_mcp import BUSINESS_TOOL_NAMES
from app.services.skill_tools import SKILL_TOOL_REGISTRY
from app.services.skills import scan_skills

router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[Depends(require_auth)])

# Claude Code's default built-in tool set. The runner does not set ``tools``,
# so the bundled CLI enables its own default set. The exact names drift with
# the bundled CLI version and plugin environment (a local 2.1.321 init frame
# returned 21 names, another environment returned DesignSync/ToolSearch too);
# this constant is the known superset used for display, and the runtime init
# frame is authoritative.
CLAUDE_CODE_TOOLS = (
    ("Task", "派生子 Agent 执行任务"),
    ("Bash", "在宿主机执行命令；没有沙箱，工作目录是当前 Agent 工作区"),
    ("CronCreate", "Claude Code 自带的定时任务创建（与平台「定时」页面无关）"),
    ("CronDelete", "Claude Code 自带的定时任务删除"),
    ("CronList", "Claude Code 自带的定时任务列表"),
    ("DesignSync", "Claude Code 内置工具（随 CLI 版本/插件环境提供）"),
    ("Edit", "精确替换文件片段"),
    ("EnterWorktree", "进入 git worktree"),
    ("ExitWorktree", "退出 git worktree"),
    ("ListAgents", "列出 Claude Code Agent"),
    ("Monitor", "后台监控事件"),
    ("NotebookEdit", "编辑 Jupyter notebook"),
    ("Read", "读取工作区文件（含图片）"),
    ("ReportFindings", "报告代码审查发现"),
    ("ScheduleWakeup", "安排稍后唤醒"),
    ("SendMessage", "向其他 Agent 或会话发送消息"),
    ("Skill", "调用 Claude Code 技能（不读取平台 skills/ 目录）"),
    ("TaskStop", "停止后台任务"),
    ("ToolSearch", "搜索可用工具（Claude Code 内置）"),
    ("WebFetch", "抓取网页内容"),
    ("WebSearch", "搜索网页"),
    ("Workflow", "运行工作流"),
    ("Write", "创建或覆盖工作区文件"),
)

CLAUDE_CODE_TOOLS_NOTE = (
    "Claude Code 默认工具集，随 bundled CLI 版本和插件环境变化；"
    "平台业务工具以 mcp__platform_tools__* 出现在运行时 init frame。"
)

# Static declaration: skill authoring stays outside the SDK runtime.
PLATFORM_TOOLS = (
    (
        "skill_save",
        "把可复用的流程保存为技能（本版尚未接入 SDK，Agent 无法调用）。",
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
            # Business tools are mounted on the in-process platform MCP
            # server; browser and web tooling stay unwired.
            wired=name in BUSINESS_TOOL_NAMES,
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
            for name, description in CLAUDE_CODE_TOOLS
        ],
        skill_tools=skill_tools,
        claude_code_note=CLAUDE_CODE_TOOLS_NOTE,
    )
