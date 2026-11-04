"""Deterministic slash commands shared by the web chat and WeChat channels.

Commands only match when the whole message is a single slash token; anything
with spaces or other text is a normal Agent message.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Agent, Run, Session

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r"^/[A-Za-z][A-Za-z0-9_-]{0,15}$")
KNOWN_COMMANDS = frozenset({"/new", "/clear", "/help", "/status", "/stop"})
ACTIVE_RUN_STATUSES = ("queued", "running")

HELP_TEXT = (
    "可用命令：\n"
    "/new 开始新会话（旧会话保留在会话列表）\n"
    "/clear 同 /new\n"
    "/status 查看当前会话与任务状态\n"
    "/stop 停止当前会话排队或运行中的任务\n"
    "/help 显示本说明\n\n"
    "直接发送其他内容即可与 Agent 对话。"
)
UNKNOWN_COMMAND_TEXT = f"未知命令。\n\n{HELP_TEXT}"
STOP_EMPTY_TEXT = "当前没有正在运行或排队的任务。"
STOP_UNAVAILABLE_TEXT = "停止任务失败：运行队列不可用，请稍后再试。"
STOP_FINISHED_TEXT = "任务已经结束，无需停止。"
NO_SESSION_STATUS_TEXT = "当前还没有会话，直接发消息即可开始新对话。"
WEB_NEW_SESSION_TEXT = "已开始新会话，之前的对话不会带入；旧会话仍保留在会话列表。"


def is_command_text(text: str) -> bool:
    """True only for a whole message that is a single slash command token."""
    return bool(_COMMAND_RE.match((text or "").strip()))


def parse_command(text: str) -> str | None:
    stripped = (text or "").strip()
    if not _COMMAND_RE.match(stripped):
        return None
    return stripped.lower()


@dataclass(frozen=True)
class CommandResult:
    command: str
    reply: str | None
    cancelled_count: int = 0


async def _active_runs(db: AsyncSession, session_id: str) -> list[Run]:
    return list(
        (
            await db.scalars(
                select(Run)
                .where(
                    Run.session_id == session_id,
                    Run.status.in_(ACTIVE_RUN_STATUSES),
                )
                .order_by(Run.created_at)
            )
        ).all()
    )


async def execute_command(
    *,
    agent_id: str,
    session_id: str | None,
    command: str,
    worker: Any = None,
) -> CommandResult:
    """Run one slash command for the web chat.

    Unlike the WeChat channel, /new does not create a Session here: the web
    view switches to a lazy empty chat and the next message creates it.
    """
    run_ids: list[str] = []
    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None or agent.status == "archived":
            raise LookupError("agent not found")
        session = None
        if session_id:
            session = await db.get(Session, session_id)
            if session is None or session.agent_id != agent_id:
                raise LookupError("session not found")

        if command not in KNOWN_COMMANDS:
            return CommandResult(command=command, reply=UNKNOWN_COMMAND_TEXT)

        if command in ("/new", "/clear"):
            active = await _active_runs(db, session.id) if session is not None else []
            reply = WEB_NEW_SESSION_TEXT
            if active:
                reply += (
                    f"\n之前还有 {len(active)} 个任务排队或运行中，"
                    "完成后仍可在旧会话查看。"
                )
            return CommandResult(command=command, reply=reply)

        if command == "/help":
            return CommandResult(command=command, reply=HELP_TEXT)

        if command == "/status":
            if session is None:
                return CommandResult(command=command, reply=NO_SESSION_STATUS_TEXT)
            active = await _active_runs(db, session.id)
            running = sum(1 for run in active if run.status == "running")
            queued = len(active) - running
            reply = (
                f"Agent：{agent.name}\n"
                f"会话：{session.title}\n"
                f"运行中：{running} 个，排队：{queued} 个"
            )
            return CommandResult(command=command, reply=reply)

        # /stop
        if session is None:
            return CommandResult(command=command, reply=STOP_EMPTY_TEXT)
        active = await _active_runs(db, session.id)
        if not active:
            return CommandResult(command=command, reply=STOP_EMPTY_TEXT)
        run_ids = [run.id for run in active]

    if worker is None:
        return CommandResult(command=command, reply=STOP_UNAVAILABLE_TEXT)

    stopped = 0
    failed = 0
    for run_id in run_ids:
        try:
            if await worker.cancel(run_id):
                stopped += 1
        except LookupError:
            continue
        except Exception:
            failed += 1
            logger.exception("web command cancel failed run=%s", run_id)

    if stopped:
        return CommandResult(
            command=command,
            reply=f"已停止 {stopped} 个任务。",
            cancelled_count=stopped,
        )
    if failed:
        return CommandResult(command=command, reply=STOP_UNAVAILABLE_TEXT)
    return CommandResult(command=command, reply=STOP_FINISHED_TEXT)
