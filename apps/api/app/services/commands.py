"""Pure slash-command parsing and user-facing command text.

Shared by the web chat router and channel intake. This module must stay free
of database, model and runtime-service imports so command handling can be used
without loading the platform's persistence layer.
"""

from __future__ import annotations

import re

_COMMAND_RE = re.compile(r"^/[A-Za-z][A-Za-z0-9_-]{0,15}$")

HELP_TEXT = (
    "可用命令：\n"
    "/new 开始新会话（旧会话保留在会话列表）\n"
    "/clear 同 /new\n"
    "/status 查看当前会话与执行状态\n"
    "/stop 停止当前任务\n"
    "/help 显示本说明\n\n"
    "直接发送其他内容即可与 Agent 对话。"
)
UNKNOWN_COMMAND_TEXT = f"未知命令。\n\n{HELP_TEXT}"
NEW_SESSION_TEXT = "已开始新会话，之前的对话不会带入；旧会话仍保留在会话列表。"
NO_SESSION_TEXT = "当前还没有会话，直接发消息即可开始新对话。"
STOP_EMPTY_TEXT = "当前没有正在执行的任务。"
STOP_REQUESTED_TEXT = "已请求停止当前任务。"


def is_command_text(text: str) -> bool:
    """True for a whole slash command."""
    return bool(_COMMAND_RE.match((text or "").strip()))


def parse_command(text: str) -> str | None:
    stripped = (text or "").strip()
    if not _COMMAND_RE.match(stripped):
        return None
    return stripped.lower()
