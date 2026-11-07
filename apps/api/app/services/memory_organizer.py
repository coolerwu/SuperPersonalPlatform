from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Run, Session

logger = logging.getLogger(__name__)

# Must stay byte-identical to MEMORY_COMMAND in apps/web/src/messageContent.ts:
# the chat UI renders the command card by exact match, and the backend resets
# the turn counter on the same text.
MEMORY_ORGANIZE_PROMPT = (
    "请整理长期记忆：先读取 /skills/memory-organizer/SKILL.md，"
    "然后按技能说明合并重复记忆、删除失效记忆，不要新增事实。"
)
MEMORY_ORGANIZER_SKILL = "memory-organizer"
MEMORY_SESSION_TITLE = "记忆整理"
COUNTED_SOURCES = ("manual", "wechat")


def is_manual_memory_command(content: str) -> bool:
    return content.strip() == MEMORY_ORGANIZE_PROMPT


async def note_user_turn(
    db: AsyncSession, *, agent_id: str, content: str, source: str
) -> Run | None:
    """Count one user turn and enqueue an organizer Run once the threshold is hit.

    Runs in the caller's transaction so the count moves together with the user
    Run. Returns the organizer Run when one was queued.
    """
    if source not in COUNTED_SOURCES:
        return None
    if source == "manual" and is_manual_memory_command(content):
        await db.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(memory_turn_count=0)
            .execution_options(synchronize_session=False)
        )
        return None

    agent = await db.get(Agent, agent_id)
    if agent is None or not agent.auto_memory:
        return None

    from app.services.app_config import get_memory_consolidate_every_turns

    threshold = await get_memory_consolidate_every_turns(db)
    if threshold <= 0:
        return None

    await db.execute(
        update(Agent)
        .where(Agent.id == agent_id)
        .values(memory_turn_count=Agent.memory_turn_count + 1)
        .execution_options(synchronize_session=False)
    )
    count = int(
        (
            await db.execute(
                select(Agent.memory_turn_count).where(Agent.id == agent_id)
            )
        ).scalar_one()
    )
    if count < threshold:
        return None

    # Only one concurrent turn may own the reset; the loser simply stops counting.
    reset = await db.execute(
        update(Agent)
        .where(Agent.id == agent_id, Agent.memory_turn_count >= threshold)
        .values(memory_turn_count=0)
        .execution_options(synchronize_session=False)
    )
    if reset.rowcount != 1:
        return None

    organizer_session = Session(
        agent_id=agent_id, title=MEMORY_SESSION_TITLE, origin="memory"
    )
    db.add(organizer_session)
    await db.flush()

    from app.services.run_service import _create_run_in_db

    run, _message = await _create_run_in_db(
        db,
        organizer_session,
        MEMORY_ORGANIZE_PROMPT,
        source="memory",
        count_turn=False,
    )
    logger.info(
        "memory consolidation queued for agent %s (run %s) after %s turns",
        agent_id,
        run.id,
        threshold,
    )
    return run
