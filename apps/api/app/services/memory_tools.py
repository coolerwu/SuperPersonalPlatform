from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool

from app.models import MemoryItem
from app.services import memory_service
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext, SkillToolRegistry

DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 400
MAX_LIST_OFFSET = 10_000
MAX_CONTENT_LENGTH = 1000
MAX_RESULT_CHARS = 60_000
VALID_SCOPES = ("all", "user", "agent")


def _can_edit(context: SkillToolContext, item: MemoryItem) -> bool:
    if item.scope == "user":
        return True
    return bool(context.agent_id) and item.scope == "agent" and item.agent_id == context.agent_id


def _scope_label(item: MemoryItem) -> str:
    return "用户" if item.scope == "user" else "Agent"


def _format_items(items: list[MemoryItem], *, total: int, offset: int) -> str:
    if not items:
        return "没有找到长期记忆。" if total == 0 else f"没有更多记忆（共 {total} 条）。"
    end = offset + len(items)
    lines = [
        f"## 长期记忆（共 {total} 条，返回第 {offset + 1}-{end} 条）",
        "",
    ]
    if end < total:
        lines.append(f"还有 {total - end} 条未返回，可用 offset={end} 继续读取。")
        lines.append("")
    for item in items:
        tags = "、".join(item.tags or []) or "无"
        lines.append(f"- id: {item.id} | scope: {_scope_label(item)} | tags: {tags}")
        lines.append(f"  content: {item.content}")
    output = "\n".join(lines)
    if len(output) > MAX_RESULT_CHARS:
        output = (
            f"{output[:MAX_RESULT_CHARS]}\n\n"
            "... [结果过大已截断，请减小 limit，或用 scope/query/offset 分段读取]"
        )
    return output


async def _list_memories(
    context: SkillToolContext, scope: str, query: str, limit: int, offset: int
) -> str:
    if scope not in VALID_SCOPES:
        return f"Error: scope must be one of {', '.join(VALID_SCOPES)}"
    if not 1 <= limit <= MAX_LIST_LIMIT:
        return f"Error: limit must be between 1 and {MAX_LIST_LIMIT}"
    if not 0 <= offset <= MAX_LIST_OFFSET:
        return f"Error: offset must be between 0 and {MAX_LIST_OFFSET}"
    if scope in ("all", "agent") and not context.agent_id:
        return "Error: current agent is not available for agent memory"

    items: list[MemoryItem] = []
    if scope in ("all", "user"):
        items.extend(await memory_service.list_memories(scope="user", query=query or None))
    if scope in ("all", "agent"):
        items.extend(
            await memory_service.list_memories(
                scope="agent", agent_id=context.agent_id, query=query or None
            )
        )
    items.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
    page = items[offset : offset + limit]
    return _format_items(page, total=len(items), offset=offset)


async def _update_memory(
    context: SkillToolContext, memory_id: str, content: str, tags: list[str] | None
) -> str:
    memory_id = str(memory_id or "").strip()
    if not memory_id:
        return "Error: memory_id is required"
    item = await memory_service.get_memory(memory_id)
    if item is None or not _can_edit(context, item):
        return "Error: memory not found"
    normalized = memory_service.normalize_content(content or "")
    if not normalized:
        return "Error: content is required"
    if len(normalized) > MAX_CONTENT_LENGTH:
        return f"Error: content must be at most {MAX_CONTENT_LENGTH} characters"
    updated = await memory_service.update_memory(
        memory_id, content=normalized, tags=tags if tags is not None else None
    )
    if updated is None:
        return "Error: memory not found"
    return f"已更新记忆 {updated.id}：{updated.content}"


async def _delete_memory(context: SkillToolContext, memory_id: str) -> str:
    memory_id = str(memory_id or "").strip()
    if not memory_id:
        return "Error: memory_id is required"
    item = await memory_service.get_memory(memory_id)
    if item is None or not _can_edit(context, item):
        return "Error: memory not found"
    old_content = item.content
    if not await memory_service.delete_memory(memory_id):
        return "Error: memory not found"
    return f"已删除记忆 {memory_id}：{old_content}"


def _make_list_tool(context: SkillToolContext) -> BaseTool:
    async def memory_list(
        scope: str = "all",
        query: str = "",
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> str:
        """列出长期记忆，返回 id、scope、标签和正文。"""
        return await _list_memories(context, scope, query, limit, offset)

    return StructuredTool.from_function(
        name="memory_list",
        description=(
            "列出长期记忆，返回每条记忆的 id、scope（用户/Agent）、标签和正文。"
            f"scope 可选 all、user、agent，默认 all（用户记忆 + 当前 Agent 记忆）；"
            f"limit 为 1-{MAX_LIST_LIMIT}，默认 {DEFAULT_LIST_LIMIT}；"
            "offset 用于翻页，结果中会提示下一批的 offset。"
        ),
        coroutine=memory_list,
    )


def _make_update_tool(context: SkillToolContext) -> BaseTool:
    async def memory_update(
        memory_id: str, content: str, tags: list[str] | None = None
    ) -> str:
        """更新一条长期记忆的正文和可选标签。"""
        return await _update_memory(context, memory_id, content, tags)

    return StructuredTool.from_function(
        name="memory_update",
        description=(
            "更新一条长期记忆的正文（最多 1000 字），可选替换标签；"
            "id 必须来自 memory_list，且只能操作用户记忆或当前 Agent 的记忆。"
        ),
        coroutine=memory_update,
    )


def _make_delete_tool(context: SkillToolContext) -> BaseTool:
    async def memory_delete(memory_id: str) -> str:
        """删除一条长期记忆。"""
        return await _delete_memory(context, memory_id)

    return StructuredTool.from_function(
        name="memory_delete",
        description=(
            "删除一条长期记忆；id 必须来自 memory_list，"
            "且只能删除用户记忆或当前 Agent 的记忆。"
        ),
        coroutine=memory_delete,
    )


def register_memory_tools(registry: SkillToolRegistry = SKILL_TOOL_REGISTRY) -> None:
    specs = (
        ("memory_list", _make_list_tool, "列出用户记忆和当前 Agent 记忆"),
        ("memory_update", _make_update_tool, "更新一条长期记忆"),
        ("memory_delete", _make_delete_tool, "删除一条长期记忆"),
    )
    for name, factory, description in specs:
        if registry.has(name):
            continue
        registry.register_factory(name, factory, description=description)


register_memory_tools()
