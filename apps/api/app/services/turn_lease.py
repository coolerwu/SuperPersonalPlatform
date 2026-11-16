"""Per-session turn lease.

A row in ``running_sessions`` means the session currently has a turn attached,
or keeps background tasks after its turn ended (``has_turn=False``). Correctness
relies on at most one turn executing per session at a time; startup recovery
reads only rows with ``has_turn=True`` to find sessions that were mid-run.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import RunningSession, utcnow

logger = logging.getLogger(__name__)


async def add_in(db: AsyncSession, session_id: str) -> None:
    """Insert or reactivate a turn lease inside the caller's transaction.

    Idempotent for a lease that is already committed/visible. Reactivating a
    task-only row preserves its retained tasks. A concurrent insert for the
    same session raises ``IntegrityError`` from the flush; the caller must roll
    back its own transaction. The executor relies on the one-turn-per-session
    invariant to avoid this. Same-session turns are serialized; cleanup only
    deletes ``has_turn=False`` rows.
    """
    existing = await db.get(RunningSession, session_id)
    if existing is not None:
        existing.has_turn = True
        existing.updated_at = utcnow()
        await db.flush()
        return
    db.add(
        RunningSession(
            session_id=session_id,
            has_turn=True,
            tasks_json="[]",
            updated_at=utcnow(),
        )
    )
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

    Callers must wrap this call in try/except so a lease-cleanup failure cannot
    interrupt turn teardown (for example publishing ``turn_end``).
    """
    async with SessionLocal() as db:
        await db.execute(
            delete(RunningSession).where(RunningSession.session_id == session_id)
        )
        await db.commit()


async def end_turn(session_id: str) -> None:
    """Release the turn part of a row, keeping retained tasks.

    The runner calls this after the turn's task events have been flushed.
    Rows with no tasks are deleted exactly like the old lease behavior.

    Teardown must call this even if the task-state flush fails; callers must
    wrap flush and ``end_turn`` in separate try blocks so a flush failure
    cannot leave ``has_turn=True`` and trigger a false resume.
    """
    async with SessionLocal() as db:
        row = await db.get(RunningSession, session_id)
        if row is None:
            return
        row.has_turn = False
        row.updated_at = utcnow()
        try:
            tasks = json.loads(row.tasks_json or "[]")
        except ValueError:
            logger.warning(
                "keeping row with malformed tasks_json session=%s",
                session_id,
                exc_info=True,
            )
            tasks = None
        if isinstance(tasks, list) and not tasks:
            await db.delete(row)
        await db.commit()


async def list_all() -> list[str]:
    async with SessionLocal() as db:
        rows = await db.scalars(select(RunningSession.session_id))
        return list(rows)


async def list_turn_leases() -> list[str]:
    """List session ids holding a real turn lease."""
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(RunningSession.session_id).where(
                RunningSession.has_turn.is_(True)
            )
        )
        return list(rows)


async def is_running(session_id: str) -> bool:
    async with SessionLocal() as db:
        row = await db.get(RunningSession, session_id)
        return row is not None and bool(row.has_turn)
