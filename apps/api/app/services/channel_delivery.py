"""Channel reply obligations.

Rows are created with empty content at turn start. An empty row is only
claimed by a resumed turn (``find_unfilled_for_session``); a row that already
holds content is only sent by the delivery worker/sweep. ``sent`` and
``failed`` are terminal and never rewritten.
"""

from __future__ import annotations

from sqlalchemy import case, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import ChannelDelivery, utcnow

_DELIVERABLE_STATUSES = ("pending", "attempting")


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


async def find_pending_for_session(session_id: str) -> ChannelDelivery | None:
    """Return the latest live delivery for a session as a read-only snapshot.

    Rows with empty content are included on purpose: callers that need a
    recovery candidate must use ``find_unfilled_for_session`` instead, so a
    filled row already owed to the sender is never reloaded. The returned
    instance is detached; callers must not mutate it.
    """
    async with SessionLocal() as db:
        return await db.scalar(
            select(ChannelDelivery)
            .where(
                ChannelDelivery.session_id == session_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
            )
            .order_by(ChannelDelivery.id.desc())
            .limit(1)
        )


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
    is left as delivery history.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            delete(ChannelDelivery).where(
                ChannelDelivery.id == delivery_id,
                ChannelDelivery.status.in_(_DELIVERABLE_STATUSES),
            )
        )
        await db.commit()
        return (result.rowcount or 0) > 0


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
