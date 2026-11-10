"""Per-session turn lease.

A row in ``running_sessions`` means the session currently has a turn attached.
Correctness relies on at most one turn executing per session at a time; startup
recovery reads this table to find sessions that were mid-run.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import RunningSession

logger = logging.getLogger(__name__)


async def add_in(db: AsyncSession, session_id: str) -> None:
    """Insert a lease row inside the caller's transaction.

    Idempotent: an existing lease for the same session is accepted silently, so
    a repeated insert does not abort the surrounding transaction. Callers that
    commit their own transaction can combine the lease with other writes.
    """
    existing = await db.get(RunningSession, session_id)
    if existing is not None:
        return
    db.add(RunningSession(session_id=session_id))
    await db.flush()


async def add(session_id: str) -> None:
    """Record that ``session_id`` has a turn attached, best-effort.

    A duplicate insert is expected when a lease already exists and is ignored.
    Other failures are logged and swallowed so they cannot fail the run.
    """
    async with SessionLocal() as db:
        try:
            await add_in(db, session_id)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.debug("turn lease already exists session=%s", session_id)
        except Exception:
            await db.rollback()
            logger.exception("failed to record turn lease session=%s", session_id)


async def remove(session_id: str) -> None:
    """Remove the lease row for a session (idempotent).

    Callers must ensure removal cannot break this turn's teardown (for example
    publishing the frontend ``turn_end`` event); the executor wraps this call in
    try/except so a failure here never aborts the rest of teardown.
    """
    async with SessionLocal() as db:
        await db.execute(
            delete(RunningSession).where(RunningSession.session_id == session_id)
        )
        await db.commit()


async def list_all() -> list[str]:
    async with SessionLocal() as db:
        rows = await db.scalars(select(RunningSession.session_id))
        return list(rows)


async def is_running(session_id: str) -> bool:
    async with SessionLocal() as db:
        row = await db.get(RunningSession, session_id)
        return row is not None
