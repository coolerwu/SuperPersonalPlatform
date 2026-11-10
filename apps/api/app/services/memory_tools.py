from __future__ import annotations

import logging

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import MemoryItem
from app.services import memory_service
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext, SkillToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 400
MAX_LIST_OFFSET = 10_000
MAX_CONTENT_LENGTH = 320
MAX_SAVES_PER_RUN = 5
MAX_RESULT_CHARS = 60_000


def _can_edit(context: SkillToolContext, item: MemoryItem) -> bool:
    return bool(
        context.agent_id
        and item.scope == "agent"
        and item.agent_id == context.agent_id
    )


def _format_items(items: list[MemoryItem], *, total: int, offset: int) -> str:
    if not items:
        return "没有找到 Agent 记忆。" if total == 0 else f"没有更多记忆（共 {total} 条）。"
    end = offset + len(items)
    lines = [
        f"## Agent 记忆（共 {total} 条，返回第 {offset + 1}-{end} 条）",
        "",
    ]
    if end < total:
        lines.append(f"还有 {total - end} 条未返回，可用 offset={end} 继续读取。")
        lines.append("")
    for item in items:
        tags = "、".join(item.tags or []) or "无"
        lines.append(f"- id: {item.id} | tags: {tags}")
        lines.append(f"  content: {item.content}")
    output = "\n".join(lines)
    if len(output) > MAX_RESULT_CHARS:
        output = (
            f"{output[:MAX_RESULT_CHARS]}\n\n"
            "... [结果过大已截断，请减小 limit，用 query/offset 分段读取]"
        )
    return output


async def _list_memories(
    context: SkillToolContext, query: str, limit: int, offset: int
) -> str:
    if not context.agent_id:
        return "Error: current agent is not available for agent memory"
    if not 1 <= limit <= MAX_LIST_LIMIT:
        return f"Error: limit must be between 1 and {MAX_LIST_LIMIT}"
    if not 0 <= offset <= MAX_LIST_OFFSET:
        return f"Error: offset must be between 0 and {MAX_LIST_OFFSET}"

    items = await memory_service.list_memories(
        scope="agent", agent_id=context.agent_id, query=query or None
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


async def _save_memory(
    context: SkillToolContext, content: str, tags: list[str] | None
) -> str:
    if not context.allow_memory_save:
        return "Error: memory_save is disabled for this Agent"
    if not context.agent_id or not context.run_id:
        return "Error: current agent or run is not available"
    normalized = memory_service.normalize_content(content or "")
    if len(normalized) < 2:
        return "Error: content is required"
    if len(normalized) > MAX_CONTENT_LENGTH:
        return f"Error: content must be at most {MAX_CONTENT_LENGTH} characters"

    async with SessionLocal() as db:
        saved_this_run = int(
            await db.scalar(
                select(func.count())
                .select_from(MemoryItem)
                .where(
                    MemoryItem.scope == "agent",
                    MemoryItem.agent_id == context.agent_id,
                    MemoryItem.source_run_id == context.run_id,
                )
            )
            or 0
        )
    if saved_this_run >= MAX_SAVES_PER_RUN:
        return f"Error: this run already saved {MAX_SAVES_PER_RUN} memories"

    message_id, message_session_id = await memory_service.user_message_for_run(
        context.run_id
    )
    item = await memory_service.create_memory(
        scope="agent",
        agent_id=context.agent_id,
        content=normalized,
        tags=tags,
        source_message_id=message_id,
        source_run_id=context.run_id,
        source_session_id=context.session_id or message_session_id,
        source_kind="agent",
    )
    if item is None:
        return "Error: memory limit reached"
    try:
        from app.services.run_service import append_event

        await append_event(
            context.run_id,
            "memory_saved",
            {
                "source": "agent",
                "scope": "agent",
                "memory_id": item.id,
                "content": item.content,
            },
        )
    except Exception:
        logger.exception("failed to record agent memory event for run %s", context.run_id)
    return f"已保存 Agent 记忆 {item.id}：{item.content}"


def _make_list_tool(context: SkillToolContext) -> BaseTool:
    async def memory_list(
        scope: str = "agent",
        query: str = "",
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> str:
        """列出当前 Agent 的长期记忆，返回 id、标签和正文。"""
        # Older SKILL.md text may still pass scope="all"; Agent memory is the
        # only supported scope now, so accept and ignore the argument.
        return await _list_memories(context, query, limit, offset)

    return StructuredTool.from_function(
        name="memory_list",
        description=(
            "列出当前 Agent 的 Agent 记忆，返回每条记忆的 id、标签和正文；不包含用户记忆。"
            f"limit 为 1-{MAX_LIST_LIMIT}，默认 {DEFAULT_LIST_LIMIT}；"
            "offset 用于翻页，结果中会提示下一批的 offset。"
        ),
        coroutine=memory_list,
    )


def _make_update_tool(context: SkillToolContext) -> BaseTool:
    async def memory_update(
        memory_id: str, content: str, tags: list[str] | None = None
    ) -> str:
        """更新当前 Agent 的一条 Agent 记忆。"""
        return await _update_memory(context, memory_id, content, tags)

    return StructuredTool.from_function(
        name="memory_update",
        description=(
            f"更新当前 Agent 的一条 Agent 记忆正文（最多 {MAX_CONTENT_LENGTH} 字），"
            "可选替换标签；不能修改用户记忆。id 必须来自 memory_list。"
        ),
        coroutine=memory_update,
    )


def _make_delete_tool(context: SkillToolContext) -> BaseTool:
    async def memory_delete(memory_id: str) -> str:
        """删除当前 Agent 的一条 Agent 记忆。"""
        return await _delete_memory(context, memory_id)

    return StructuredTool.from_function(
        name="memory_delete",
        description=(
            "删除当前 Agent 的一条 Agent 记忆；不能删除用户记忆。"
            "id 必须来自 memory_list。"
        ),
        coroutine=memory_delete,
    )


def build_memory_save_tool(context: SkillToolContext) -> BaseTool:
    async def memory_save(content: str, tags: list[str] | None = None) -> str:
        """记录一条当前 Agent 的长期记忆。"""
        return await _save_memory(context, content, tags)

    return StructuredTool.from_function(
        name="memory_save",
        description=(
            "记录一条当前 Agent 的长期记忆。只写以后还会用到的稳定结论、"
            "项目/领域知识或工作方式；不要记录一次性的对话细节、新闻查询、"
            f"对用户偏好的猜测，也不要写密码或密钥。content 最多 {MAX_CONTENT_LENGTH} 字，"
            f"每次 Run 最多 {MAX_SAVES_PER_RUN} 条。"
        ),
        coroutine=memory_save,
    )


async def _read_memory(context: SkillToolContext, memory_id: str) -> str:
    memory_id = str(memory_id or "").strip()
    if not memory_id:
        return "Error: memory_id is required"
    item = await memory_service.get_memory(memory_id)
    if item is None or not _can_edit(context, item):
        return "Error: memory not found"
    await memory_service.touch_memory(memory_id)
    if context.run_id:
        try:
            await memory_service.record_run_memories(context.run_id, [memory_id])
        except Exception:
            logger.exception("failed to record memory read for run %s", context.run_id)
    tags = "、".join(item.tags or []) or "无"
    return f"## Agent 记忆 {item.id}\n标签：{tags}\n\n{item.content}"


async def _search_memory(
    context: SkillToolContext, query: str, limit: int
) -> str:
    if not context.agent_id:
        return "Error: current agent is not available for agent memory"
    if not 1 <= limit <= 20:
        return "Error: limit must be between 1 and 20"
    needle = str(query or "").strip()
    if len(needle) < 2:
        return "Error: query is required (at least 2 characters)"
    items = await memory_service.list_memories(
        scope="agent", agent_id=context.agent_id, query=needle
    )
    if not items:
        return "没有找到匹配的 Agent 记忆。"
    items.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
    lines = [f"## Agent 记忆搜索结果（{min(len(items), limit)}/{len(items)} 条）", ""]
    for item in items[:limit]:
        snippet = memory_service.normalize_content(item.content)
        if len(snippet) > 120:
            snippet = snippet[:120].rstrip() + "…"
        tags = "、".join(item.tags or []) or "无"
        lines.append(f"- id: {item.id} | tags: {tags} | {snippet}")
    lines.extend(["", "需要全文时调用 memory_read(memory_id)。"])
    return "\n".join(lines)


def build_memory_read_tool(context: SkillToolContext) -> BaseTool:
    async def memory_read(memory_id: str) -> str:
        """读取一条当前 Agent 的 Agent 记忆全文。"""
        return await _read_memory(context, memory_id)

    return StructuredTool.from_function(
        name="memory_read",
        description=(
            "读取一条当前 Agent 的 Agent 记忆全文；memory_id 来自 Agent 记忆索引"
            "或 memory_search 的结果。不能读取用户记忆或其他 Agent 的记忆。"
        ),
        coroutine=memory_read,
    )


def build_memory_search_tool(context: SkillToolContext) -> BaseTool:
    async def memory_search(query: str, limit: int = 10) -> str:
        """按关键词搜索当前 Agent 的 Agent 记忆。"""
        return await _search_memory(context, query, limit)

    return StructuredTool.from_function(
        name="memory_search",
        description=(
            "按关键词搜索当前 Agent 的 Agent 记忆，返回 id、标签和摘要；"
            "需要全文时再用 memory_read(memory_id)。只搜索当前 Agent 的 Agent 记忆。"
        ),
        coroutine=memory_search,
    )


def register_memory_tools(registry: SkillToolRegistry = SKILL_TOOL_REGISTRY) -> None:
    specs = (
        ("memory_list", _make_list_tool, "列出当前 Agent 的 Agent 记忆"),
        ("memory_update", _make_update_tool, "更新一条 Agent 记忆"),
        ("memory_delete", _make_delete_tool, "删除一条 Agent 记忆"),
    )
    for name, factory, description in specs:
        if registry.has(name):
            continue
        registry.register_factory(name, factory, description=description)


register_memory_tools()
