import json
import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import SessionLocal, _ensure_running_session_columns, init_db
from app.models import RunningSession
from app.services import turn_lease

SESSION_ID = "11111111-1111-1111-1111-111111111111"


def _task(task_id: str, status: str = "running") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "task_id": task_id,
        "tool_use_id": None,
        "description": task_id,
        "status": status,
        "summary": "",
        "last_tool_name": "",
        "usage": None,
        "started_at": now,
        "updated_at": now,
        "completed_at": now if status != "running" else None,
    }


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
    committed_id = "session-add-in-commit"
    rolled_back_id = "session-add-in-rollback"
    try:
        async with SessionLocal() as db:
            await turn_lease.add_in(db, committed_id)
            await turn_lease.add_in(db, committed_id)
            await db.commit()
        assert await turn_lease.is_running(committed_id)

        async with SessionLocal() as db:
            await turn_lease.add_in(db, rolled_back_id)
            await db.rollback()
        assert not await turn_lease.is_running(rolled_back_id)
    finally:
        await turn_lease.remove(committed_id)
        await turn_lease.remove(rolled_back_id)


@pytest.mark.asyncio
async def test_begin_turn_and_end_turn_keep_task_row():
    await init_db()
    await turn_lease.add(SESSION_ID)
    try:
        async with SessionLocal() as db:
            row = await db.get(RunningSession, SESSION_ID)
            row.tasks_json = json.dumps([_task("t1")])
            await db.commit()

        await turn_lease.end_turn(SESSION_ID)

        async with SessionLocal() as db:
            row = await db.get(RunningSession, SESSION_ID)
            assert row is not None
            assert row.has_turn is False
            assert json.loads(row.tasks_json)[0]["task_id"] == "t1"
    finally:
        await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_end_turn_deletes_empty_row():
    await init_db()
    await turn_lease.add(SESSION_ID)
    try:
        await turn_lease.end_turn(SESSION_ID)

        async with SessionLocal() as db:
            assert await db.get(RunningSession, SESSION_ID) is None
    finally:
        await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_list_turn_leases_ignores_task_only_rows():
    await init_db()
    other = "22222222-2222-2222-2222-222222222222"
    await turn_lease.add(SESSION_ID)
    try:
        async with SessionLocal() as db:
            db.add(
                RunningSession(
                    session_id=other,
                    has_turn=False,
                    tasks_json=json.dumps([_task("t2")]),
                )
            )
            await db.commit()

        leases = await turn_lease.list_turn_leases()
        assert SESSION_ID in leases
        assert other not in leases
        assert await turn_lease.is_running(SESSION_ID) is True
        assert await turn_lease.is_running(other) is False
    finally:
        await turn_lease.remove(SESSION_ID)
        await turn_lease.remove(other)


@pytest.mark.asyncio
async def test_begin_turn_reactivates_task_row():
    await init_db()
    await turn_lease.add(SESSION_ID)
    try:
        async with SessionLocal() as db:
            row = await db.get(RunningSession, SESSION_ID)
            row.tasks_json = json.dumps([_task("t1")])
            await db.commit()

        await turn_lease.end_turn(SESSION_ID)
        async with SessionLocal() as db:
            row = await db.get(RunningSession, SESSION_ID)
            assert row is not None
            assert row.has_turn is False

        await turn_lease.add(SESSION_ID)

        async with SessionLocal() as db:
            row = await db.get(RunningSession, SESSION_ID)
            assert row.has_turn is True
            assert json.loads(row.tasks_json)[0]["task_id"] == "t1"
    finally:
        await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_end_turn_keeps_row_with_malformed_tasks_json(caplog):
    await init_db()
    await turn_lease.add(SESSION_ID)
    try:
        async with SessionLocal() as db:
            row = await db.get(RunningSession, SESSION_ID)
            row.tasks_json = "not-json"
            await db.commit()

        with caplog.at_level(logging.WARNING):
            await turn_lease.end_turn(SESSION_ID)

        async with SessionLocal() as db:
            row = await db.get(RunningSession, SESSION_ID)
            assert row is not None
            assert row.has_turn is False
            assert row.tasks_json == "not-json"
        assert "malformed tasks_json" in caplog.text
    finally:
        await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_end_turn_missing_row_is_idempotent():
    await init_db()
    await turn_lease.remove(SESSION_ID)

    await turn_lease.end_turn(SESSION_ID)

    async with SessionLocal() as db:
        assert await db.get(RunningSession, SESSION_ID) is None


@pytest.mark.asyncio
async def test_ensure_running_session_columns_migrates_old_table(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'old.db'}", future=True
    )
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE running_sessions (session_id VARCHAR(36) PRIMARY KEY)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO running_sessions (session_id) VALUES ('old-session')"
        )
        await _ensure_running_session_columns(conn)
        result = await conn.exec_driver_sql("PRAGMA table_info(running_sessions)")
        columns = {row[1] for row in result.fetchall()}
        assert {"has_turn", "tasks_json", "updated_at"} <= columns
        result = await conn.exec_driver_sql(
            "SELECT has_turn, tasks_json, updated_at FROM running_sessions "
            "WHERE session_id = 'old-session'"
        )
        row = result.fetchone()
        assert row[0] == 1
        assert row[1] == "[]"
        assert row[2] is not None
        first_updated_at = row[2]

        await _ensure_running_session_columns(conn)
        result = await conn.exec_driver_sql(
            "SELECT updated_at FROM running_sessions WHERE session_id = 'old-session'"
        )
        assert result.fetchone()[0] == first_updated_at
    await engine.dispose()
