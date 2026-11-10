import pytest

from app.db import SessionLocal, init_db
from app.services import turn_lease


@pytest.mark.asyncio
async def test_lease_add_list_remove():
    await init_db()
    await turn_lease.add("session-a")
    assert "session-a" in await turn_lease.list_all()
    assert await turn_lease.is_running("session-a")
    await turn_lease.remove("session-a")
    assert not await turn_lease.is_running("session-a")
