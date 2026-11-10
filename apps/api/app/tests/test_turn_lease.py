import pytest

from app.db import SessionLocal, init_db
from app.services import turn_lease


@pytest.mark.asyncio
async def test_lease_add_list_remove():
    await init_db()
    session_id = "session-a"
    try:
        await turn_lease.add(session_id)
        assert session_id in await turn_lease.list_all()
        assert await turn_lease.is_running(session_id)
        await turn_lease.remove(session_id)
        assert not await turn_lease.is_running(session_id)
    finally:
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_lease_add_is_idempotent():
    await init_db()
    session_id = "session-duplicate"
    try:
        await turn_lease.add(session_id)
        await turn_lease.add(session_id)
        assert (await turn_lease.list_all()).count(session_id) == 1
        assert await turn_lease.is_running(session_id)
    finally:
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_lease_remove_is_idempotent():
    await init_db()
    session_id = "session-remove-twice"
    try:
        await turn_lease.add(session_id)
        await turn_lease.remove(session_id)
        await turn_lease.remove(session_id)
        assert not await turn_lease.is_running(session_id)
    finally:
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_add_in_joins_caller_transaction():
    await init_db()
    session_id = "session-add-in"
    try:
        async with SessionLocal() as db:
            await turn_lease.add_in(db, session_id)
            await turn_lease.add_in(db, session_id)
            await db.commit()
        assert await turn_lease.is_running(session_id)
    finally:
        await turn_lease.remove(session_id)
