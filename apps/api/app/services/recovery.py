"""Best-effort startup recovery for sessions interrupted by a restart.

The app reads ``running_sessions`` at startup. For each lease we look for a
Claude SDK transcript under one of the configured agents' project directories
and, when one exists, submit a ``Turn`` with ``source="resume"``. The runner
turns that into a "continue after restart" prompt: the SDK conversation is
kept, and the model is told not to repeat steps that already completed. A
lease without a transcript (the lease was written but the SDK had not written
its transcript yet, or the session was deleted) is dropped, as is a lease
whose session id is not a canonical UUID, because the id becomes a path
component.

The lease table has no ``agent_id``, so ownership is inferred from which
agent's project directory holds the transcript; the first match in settings
order wins, which is unambiguous for the current single-Agent deployment.
Agents whose id is not filesystem-safe are skipped before that id is spliced
into a path. Recovery is best-effort: a bad row is logged and skipped so the
remaining leases still resume, and a failure to load settings or leases, or a
settings file that yields no agents, aborts only this pass without deleting
leases.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services import turn_lease
from app.services.claude_runner import Turn, transcript_path
from app.services.identifiers import is_canonical_uuid, valid_agent_id

logger = logging.getLogger(__name__)


async def _recover_session(
    registry: Any, agents: dict[str, Any], session_id: str, home: Path | None
) -> None:
    if not is_canonical_uuid(session_id):
        logger.warning("dropping lease with invalid session id %r", session_id)
        await turn_lease.remove(session_id)
        return
    found = None
    for agent in agents.values():
        if not valid_agent_id(agent.id):
            logger.warning("skipping agent with invalid id %r", agent.id)
            continue
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
    if not settings.agents:
        # An unreadable or corrupt settings file parses to zero agents. Dropping
        # every lease then would erase sessions that are still recoverable, so
        # leave them in place and retry on the next startup.
        logger.warning("recovery skipped: no agents configured")
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
