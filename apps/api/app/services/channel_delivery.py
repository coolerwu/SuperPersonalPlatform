from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import ChannelDelivery, utcnow


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


async def complete(delivery_id: int, content: str) -> None:
    async with SessionLocal() as db:
        row = await db.get(ChannelDelivery, delivery_id)
        if row is None or row.status == "sent":
            return
        row.content = content
        row.status = "pending"
        await db.commit()


async def mark_attempting(delivery_id: int) -> None:
    async with SessionLocal() as db:
        row = await db.get(ChannelDelivery, delivery_id)
        if row is None:
            return
        row.status = "attempting"
        row.attempts = int(row.attempts or 0) + 1
        row.last_error = None
        await db.commit()


async def mark_sent(delivery_id: int) -> None:
    async with SessionLocal() as db:
        row = await db.get(ChannelDelivery, delivery_id)
        if row is None:
            return
        row.status = "sent"
        row.last_error = None
        row.delivered_at = utcnow()
        await db.commit()


async def mark_pending(
    delivery_id: int, error: str, *, max_attempts: int
) -> None:
    async with SessionLocal() as db:
        row = await db.get(ChannelDelivery, delivery_id)
        if row is None or row.status == "sent":
            return
        row.last_error = error[:2000]
        row.status = "failed" if int(row.attempts or 0) >= max_attempts else "pending"
        await db.commit()


async def list_pending() -> list[ChannelDelivery]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(ChannelDelivery)
            .where(ChannelDelivery.status.in_(("pending", "attempting")))
            .order_by(ChannelDelivery.id)
        )
        return list(rows)
