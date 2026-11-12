"""Channel reply obligations.

Channel intake creates a ``queued`` row before submitting the turn; the runner
promotes it to ``pending`` in the same transaction as the lease when the turn
actually starts. A crash while the turn is still queued leaves a queued row
with no lease, which startup answers with an explicit failure text; a crash
after the turn started leaves a lease plus a pending row and recovery resumes
that session.

Rows are created with empty content at turn start. An empty row is only
claimed by a resumed turn (``find_unfilled_for_session``); a row that already
holds content is only sent by the delivery worker/sweep. ``sent`` and
``failed`` are terminal and never rewritten.

Known limitation: a hard crash between lease removal and ``complete`` can
leave an unfilled row with no owning lease. Recovery answers rows that still
have a lease; startup backfill (``list_unfilled_session_ids`` +
``fail_unfilled_for_session``) fills lease-less empty rows with the same
explicit failure text so the startup sweep can deliver it.
"""

from __future__ import annotations

from sqlalchemy import case, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import ChannelDelivery, utcnow

_DELIVERABLE_STATUSES = ("pending", "attempting")
# Rows the platform may still cancel: the queued obligation created at ingest
# plus the live deliverable states.
_CANCELLABLE_STATUSES = ("queued", "pending", "attempting")


async def create_queued(
    db: AsyncSession, *, channel_id: str, external_id: str, session_id: str
) -> int:
    """Record the reply this inbound channel message will owe.

    Called by channel intake before the turn is submitted so a crash while the
    turn is still queued cannot lose the message silently.
    """
    row = ChannelDelivery(
        channel_id=channel_id,
        external_id=external_id,
        session_id=session_id,
        status="queued",
    )
    db.add(row)
    await db.flush()
    return int(row.id)


async def mark_started_in(db: AsyncSession, delivery_id: int) -> bool:
    """Promote a queued obligation to ``pending`` in the caller's transaction.

    Returns False when the row is no longer queued (already promoted, answered
    or removed), so the turn runs without owning an obligation.
    """
    result = await db.execute(
        update(ChannelDelivery)
        .where(
            ChannelDelivery.id == delivery_id,
            ChannelDelivery.status == "queued",
        )
        .values(status="pending", updated_at=utcnow())
    )
    return (result.rowcount or 0) > 0


async def create_pending(
    db: AsyncSession, *, channel_id: str, external_id: str, session_id: str
) -> int:
    row = ChannelDelivery(
        channel_id=channel_id,
        external_id=external_id,
        session_id=session_id,
        status="pending",
    )
    db.add(row)
    await db.flush()
    return int(row.id)


async def get(delivery_id: int) -> ChannelDelivery | None:
    async with SessionLocal() as db:
        return await db.get(ChannelDelivery, delivery_id)


async def find_unfilled_for_session(session_id: str) -> ChannelDelivery | None:
    """Return the newest live delivery for a session that still has no answer.

    Only an empty obligation may be reattached to a resumed turn; a row with
    content is already scheduled for the sender and must not be reloaded. The
    returned instance is detached; callers must not mutate it.
    """
    async with SessionLocal() as db:
        return await db.scalar(
            select(ChannelDelivery)
            .where(
                ChannelDelivery.session_id == session_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
                ChannelDelivery.content == "",
            )
            .order_by(ChannelDelivery.id.desc())
            .limit(1)
        )


async def complete(delivery_id: int, content: str) -> bool:
    """Fill the final answer while keeping the row deliverable.

    ``sent`` and ``failed`` are terminal, so a late completion never rewrites
    them. Returns whether the update applied.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            update(ChannelDelivery)
            .where(
                ChannelDelivery.id == delivery_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
            )
            .values(content=content, updated_at=utcnow())
        )
        await db.commit()
        return (result.rowcount or 0) > 0


async def discard(delivery_id: int) -> bool:
    """Drop an obligation that will never be sent (the user stopped the turn).

    Only live rows are removed; a row the sender already claimed or finished
    is left as delivery history. A ``queued`` row has no lease yet, so this is
    also the cleanup path for a turn that failed to submit.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            delete(ChannelDelivery).where(
                ChannelDelivery.id == delivery_id,
                ChannelDelivery.status.in_(_CANCELLABLE_STATUSES),
            )
        )
        await db.commit()
        return (result.rowcount or 0) > 0


async def list_unfilled_session_ids() -> list[str]:
    """Session ids of live delivery rows that still have no answer."""
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(ChannelDelivery.session_id)
            .where(
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
                ChannelDelivery.content == "",
            )
            .distinct()
        )
        return [str(item) for item in rows.all()]


async def list_queued_session_ids() -> list[str]:
    """Session ids of queued rows whose turn never started."""
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(ChannelDelivery.session_id)
            .where(
                ChannelDelivery.status == "queued",
                ChannelDelivery.content == "",
            )
            .distinct()
        )
        return [str(item) for item in rows.all()]


async def fail_queued_for_session(session_id: str, content: str, error: str) -> int:
    """Answer queued obligations whose turn never started before a restart.

    The row was created at ingest but never promoted, so no lease exists for
    it. Moving it to ``pending`` with the failure text lets the normal startup
    sweep tell the contact instead of losing the message to iLink dedup.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            update(ChannelDelivery)
            .where(
                ChannelDelivery.session_id == session_id,
                ChannelDelivery.status == "queued",
                ChannelDelivery.content == "",
            )
            .values(
                status="pending",
                content=content,
                last_error=str(error)[:2000],
                updated_at=utcnow(),
            )
        )
        await db.commit()
        return int(result.rowcount or 0)


async def fail_unfilled_for_session(
    session_id: str, content: str, error: str
) -> int:
    """Answer an empty obligation after its turn was lost in a restart.

    Recovery removes the lease but must not drop the channel user's message
    silently. Filling the row keeps it deliverable, so the normal startup
    sweep sends the failure text; the row intentionally stays ``pending`` (no
    partial-send prefix) because nothing was ever sent.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            update(ChannelDelivery)
            .where(
                ChannelDelivery.session_id == session_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
                ChannelDelivery.content == "",
            )
            .values(
                content=content,
                last_error=str(error)[:2000],
                updated_at=utcnow(),
            )
        )
        await db.commit()
        return int(result.rowcount or 0)


async def has_for_session(session_id: str) -> bool:
    async with SessionLocal() as db:
        row = await db.scalar(
            select(ChannelDelivery.id)
            .where(ChannelDelivery.session_id == session_id)
            .limit(1)
        )
        return row is not None


async def delete_for_session(session_id: str) -> int:
    """Delete every delivery row owned by ``session_id``.

    Used when the session itself is gone (manual delete or retention cleanup);
    its delivery history can no longer be referenced.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            delete(ChannelDelivery).where(ChannelDelivery.session_id == session_id)
        )
        await db.commit()
        return int(result.rowcount or 0)


async def mark_attempting(delivery_id: int) -> bool:
    """Atomically claim a delivery for one send attempt.

    Returns False when another attempt already moved the row to a terminal
    state, so the caller must not send it again.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            update(ChannelDelivery)
            .where(
                ChannelDelivery.id == delivery_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
            )
            .values(
                status="attempting",
                attempts=ChannelDelivery.attempts + 1,
                last_error=None,
                updated_at=utcnow(),
            )
        )
        await db.commit()
        return (result.rowcount or 0) > 0


async def mark_attempting_error(
    delivery_id: int, error: str, *, max_attempts: int
) -> bool:
    """Record a failed send after at least one chunk was already sent.

    ``mark_attempting`` already counted this send attempt, matching the
    no-chunk ``mark_pending`` path. Below the limit the row stays
    ``attempting`` so the next sweep keeps the "possibly partially delivered"
    prefix; at the limit it becomes terminal ``failed`` and is no longer
    retried automatically. Only live rows are updated.
    """
    next_status = case(
        (ChannelDelivery.attempts >= max_attempts, "failed"),
        else_="attempting",
    )
    async with SessionLocal() as db:
        result = await db.execute(
            update(ChannelDelivery)
            .where(
                ChannelDelivery.id == delivery_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
            )
            .values(
                last_error=str(error)[:2000],
                status=next_status,
                updated_at=utcnow(),
            )
        )
        await db.commit()
        return (result.rowcount or 0) > 0


async def mark_sent(delivery_id: int) -> bool:
    """Mark a live delivery as sent; terminal rows are left untouched."""
    async with SessionLocal() as db:
        result = await db.execute(
            update(ChannelDelivery)
            .where(
                ChannelDelivery.id == delivery_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
            )
            .values(
                status="sent",
                last_error=None,
                delivered_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        await db.commit()
        return (result.rowcount or 0) > 0


async def mark_pending(delivery_id: int, error: str, *, max_attempts: int) -> bool:
    """Record a failed attempt, moving the row to ``failed`` at the limit.

    ``sent`` and ``failed`` are terminal; callers get False for those rows.
    """
    next_status = case(
        (ChannelDelivery.attempts >= max_attempts, "failed"),
        else_="pending",
    )
    async with SessionLocal() as db:
        result = await db.execute(
            update(ChannelDelivery)
            .where(
                ChannelDelivery.id == delivery_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
            )
            .values(
                last_error=str(error)[:2000],
                status=next_status,
                updated_at=utcnow(),
            )
        )
        await db.commit()
        return (result.rowcount or 0) > 0


async def list_pending() -> list[ChannelDelivery]:
    """Return deliverable rows that already hold a final answer.

    Rows are created empty at turn start, so only rows completed with content
    may be swept; terminal rows and unfinished turns are excluded.
    """
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(ChannelDelivery)
            .where(
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
                ChannelDelivery.content != "",
            )
            .order_by(ChannelDelivery.id)
        )
        return list(rows)
