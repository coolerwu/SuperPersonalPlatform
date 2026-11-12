"""Best-effort startup recovery for sessions interrupted by a restart.

The app reads ``running_sessions`` at startup. For each lease we look for a
Claude SDK transcript under one of the configured agents' project directories
and, when one exists, submit a ``Turn`` with ``source="resume"``. Only active
agents are resumable: a transcript owned by a paused or archived agent keeps
its lease untouched, so re-enabling the agent resumes it on a later startup
(the other runtimes also leave paused agents idle). The runner turns that into
a "continue after restart" prompt: the SDK conversation is kept, and the model
is told not to repeat steps that already completed. A lease without a
transcript (the lease was written but the SDK had not written its transcript
yet, or the session was deleted) is dropped after answering its delivery
obligation, as is a lease whose session id is not a canonical UUID, because
the id becomes a path component.

The lease table has no ``agent_id``, so ownership is inferred from which
agent's project directory holds the transcript; the first match in settings
order wins, which is unambiguous for the current single-Agent deployment.
Agents whose id is not filesystem-safe are skipped before that id is spliced
into a path. Recovery is best-effort: a bad row is logged and skipped so the
remaining leases still resume, and a failure to load settings or leases, or a
settings file that yields no agents, aborts only this pass without deleting
leases.

A lease without a transcript is dropped, but never silently: an empty channel
obligation is filled with an explicit failure text so the normal startup
sweep can tell that contact their message was not processed, and a web turn
(which has no delivery row) is reported with a warning log entry.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services import channel_delivery, turn_lease
from app.services.claude_runner import Turn, transcript_path
from app.services.identifiers import is_canonical_uuid, valid_agent_id

logger = logging.getLogger(__name__)

LOST_TURN_TEXT = (
    "服务重启时这条消息尚未开始处理，请重新发送。"
    "（消息未丢失，但本次没有生成回复。）"
)


async def _drop_lease(session_id: str) -> None:
    """Remove a lease after answering any obligation it left behind.

    A crash before the SDK wrote a transcript means the turn never ran. A
    channel message still has an empty delivery row; filling it with an
    explicit failure keeps it deliverable. Filled rows are left to the sender;
    a web turn has no delivery row, so its loss is recorded as a warning log.
    """
    answered = 0
    try:
        answered = await channel_delivery.fail_unfilled_for_session(
            session_id, LOST_TURN_TEXT, "turn ended before its transcript existed"
        )
    except Exception:
        logger.exception(
            "recovery could not answer deliveries session=%s", session_id
        )
    if not answered:
        try:
            has_delivery = await channel_delivery.has_for_session(session_id)
        except Exception:
            has_delivery = False
            logger.exception(
                "recovery could not inspect deliveries session=%s", session_id
            )
        if not has_delivery:
            logger.warning("web turn lost on restart session=%s", session_id)
    await turn_lease.remove(session_id)


async def _recover_session(
    registry: Any, agents: dict[str, Any], session_id: str, home: Path | None
) -> None:
    if not is_canonical_uuid(session_id):
        logger.warning("dropping lease with invalid session id %r", session_id)
        await _drop_lease(session_id)
        return
    found = None
    inactive_owner = None
    for agent in agents.values():
        if not valid_agent_id(agent.id):
            logger.warning("skipping agent with invalid id %r", agent.id)
            continue
        if not transcript_path(session_id, agent.id, home=home).is_file():
            continue
        if agent.status != "active":
            inactive_owner = agent
            continue
        found = agent
        break
    if found is None:
        if inactive_owner is not None:
            # Keep the lease: re-enabling the agent should resume the session
            # on a later startup, matching the other runtimes' paused behavior.
            logger.warning(
                "keeping lease owned by inactive agent %r session=%s",
                inactive_owner.id,
                session_id,
            )
            return
        logger.info("dropping lease without transcript session=%s", session_id)
        await _drop_lease(session_id)
        return
    await registry.submit(
        Turn(
            session_id=session_id,
            agent_id=found.id,
            prompt="",
            source="resume",
        )
    )


async def _answer_orphan_deliveries() -> int:
    """Fill empty delivery rows that have no lease at all.

    A crash between lease removal and completion can leave such a row behind;
    ``recover_running_sessions`` only sees lease-backed rows, so startup also
    scans unfilled rows and answers those without a lease using the same
    explicit failure text. A live lease is left to its resumed turn.
    """
    try:
        session_ids = await channel_delivery.list_unfilled_session_ids()
    except Exception:
        logger.exception("recovery could not list unfilled deliveries")
        return 0
    answered = 0
    for session_id in session_ids:
        try:
            if await turn_lease.is_running(session_id):
                continue
            answered += await channel_delivery.fail_unfilled_for_session(
                session_id,
                LOST_TURN_TEXT,
                "delivery has no owning turn lease",
            )
        except Exception:
            logger.exception(
                "recovery could not answer orphan delivery session=%s", session_id
            )
    if answered:
        logger.warning("recovery answered %d orphan channel delivery row(s)", answered)
    return answered


async def _answer_queued_deliveries() -> int:
    """Answer obligations whose turn never started before a restart.

    A row stays ``queued`` from ingest until the runner adopts it at turn
    start. A crash or graceful shutdown in that window leaves no lease behind,
    so recovery fills the row with the explicit failure text and lets the
    startup sweep tell the contact instead of losing the message to iLink
    dedup (which would treat the replayed inbound id as a duplicate).
    """
    try:
        session_ids = await channel_delivery.list_queued_session_ids()
    except Exception:
        logger.exception("recovery could not list queued deliveries")
        return 0
    answered = 0
    for session_id in session_ids:
        try:
            answered += await channel_delivery.fail_queued_for_session(
                session_id,
                LOST_TURN_TEXT,
                "queued turn lost on restart",
            )
        except Exception:
            logger.exception(
                "recovery could not answer queued delivery session=%s", session_id
            )
    if answered:
        logger.warning("recovery answered %d queued channel delivery row(s)", answered)
    return answered


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
    try:
        await _answer_queued_deliveries()
    except Exception:
        logger.exception("recovery queued delivery backfill failed")
    try:
        await _answer_orphan_deliveries()
    except Exception:
        logger.exception("recovery orphan delivery backfill failed")
