import pytest

from app.db import SessionLocal, init_db
from app.services import channel_delivery


@pytest.mark.asyncio
async def test_pending_to_delivered():
    await init_db()
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db,
            channel_id="ch1",
            external_id="wx-1",
            session_id="11111111-1111-1111-1111-111111111111",
        )
        await db.commit()
    row = await channel_delivery.get(delivery_id)
    assert row is not None and row.status == "pending"
    await channel_delivery.complete(delivery_id, "hello")
    # complete() fills the content but keeps the row deliverable; the sender
    # records the attempt and final sent state.
    row = await channel_delivery.get(delivery_id)
    assert row is not None and row.status == "pending" and row.content == "hello"
    await channel_delivery.mark_attempting(delivery_id)
    await channel_delivery.mark_sent(delivery_id)
    row = await channel_delivery.get(delivery_id)
    assert row is not None and row.status == "sent" and row.content == "hello"


@pytest.mark.asyncio
async def test_pending_list_orders_by_id():
    await init_db()
    async with SessionLocal() as db:
        first = await channel_delivery.create_pending(
            db, channel_id="ch1", external_id="wx-1", session_id="s1"
        )
        second = await channel_delivery.create_pending(
            db, channel_id="ch1", external_id="wx-1", session_id="s1"
        )
        await db.commit()
    rows = await channel_delivery.list_pending()
    ids = [row.id for row in rows]
    assert ids.index(first) < ids.index(second)
