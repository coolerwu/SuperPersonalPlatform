from __future__ import annotations

# Must stay byte-identical to MEMORY_COMMAND in apps/web/src/messageContent.ts:
# the chat UI renders the command card by exact match.
MEMORY_ORGANIZE_PROMPT = (
    "请整理当前 Agent 的长期记忆：先读取 /skills/memory-organizer/SKILL.md，"
    "然后按技能说明只整理 Agent 记忆，合并重复、删除失效项，不要碰用户记忆，不要新增事实。"
)
MEMORY_ORGANIZER_SKILL = "memory-organizer"
