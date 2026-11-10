"""Best-effort startup recovery for sessions interrupted by a restart.

The app reads ``running_sessions`` at startup. For each lease we look for a
Claude SDK transcript under one of the configured agents' project directories
and, when one exists, submit a ``Turn`` with ``source="resume"``. The runner
turns that into a "continue after restart" prompt: the SDK conversation is
kept, and the model is told not to repeat steps that already completed. A
lease without a transcript (the process crashed before dispatch, or the
session was deleted) is dropped, as is a lease whose session id is not a
canonical UUID, because the id becomes a path component. Recovery is
best-effort: a bad row is logged and skipped so the remaining leases still
resume, and a failure to load settings or leases aborts only this pass.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from app.services import turn_lease
from app.services.claude_runner import Turn, transcript_path

logger = logging.getLogger(__name__)


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


async def _recover_session(
    registry: Any, agents: dict[str, Any], session_id: str, home: Path | None
) -> None:
    if not _is_canonical_uuid(session_id):
        logger.warning("dropping lease with invalid session id")
        await turn_lease.remove(session_id)
        return
    found = None
    for agent in agents.values():
        if transcript_path(session_id, agent.id, home=home).is_file():
            found = agent
            break
    if found is None:
        logger.info("dropping lease without transcript session=%s", session_id)
        await turn_lease.remove(session_id)
        return
    await registry.submit(
        Turn(
            session_id=session_id,
            agent_id=found.id,
            prompt="",
            source="resume",
        )
    )


async def recover_running_sessions(
    registry: Any, store: Any, *, home: Path | None = None
) -> None:
    try:
        settings = await store.get()
    except Exception:
        logger.exception("recovery could not load settings")
        return
    agents = {agent.id: agent for agent in settings.agents}
    try:
        session_ids = await turn_lease.list_all()
    except Exception:
        logger.exception("recovery could not read running sessions")
        return
    for session_id in session_ids:
        try:
            await _recover_session(registry, agents, session_id, home)
        except Exception:
            logger.exception("recovery failed session=%s", session_id)
