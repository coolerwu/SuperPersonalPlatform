import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SessionLocal, init_db
from app.models import RunningSession
from app.services import turn_lease
from app.services.session_tasks import SessionTaskService

SESSION_ID = "33333333-3333-3333-3333-333333333333"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _service(now=None, retention_seconds=600):
    return SessionTaskService(
        now=now or (lambda: NOW),
        retention_seconds=retention_seconds,
        cleanup_interval_seconds=3600,
    )


def _started(task_id="t1"):
    return {
        "type": "task_started",
        "task_id": task_id,
        "tool_use_id": "toolu_1",
        "description": "回测 BTC 1h",
    }


@pytest.mark.asyncio
async def test_publish_merges_lifecycle_into_snapshot():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()

    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {
            "type": "task_progress",
            "task_id": "t1",
            "description": "回测进行中",
            "last_tool_name": "Bash",
            "usage": {"tool_uses": 2},
        },
    )
    await service.publish(
        SESSION_ID,
        {
            "type": "task_notification",
            "task_id": "t1",
            "status": "completed",
            "summary": "完成",
            "usage": {"tool_uses": 3},
        },
    )

    snapshot = await service.snapshot(SESSION_ID)
    assert snapshot["has_turn"] is True
    assert len(snapshot["tasks"]) == 1
    task = snapshot["tasks"][0]
    assert task["task_id"] == "t1"
    assert task["status"] == "completed"
    assert task["summary"] == "完成"
    assert task["last_tool_name"] == "Bash"
    assert task["usage"] == {"tool_uses": 3}
    assert task["completed_at"] is not None
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_task_updated_terminal_without_notification():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()

    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {"type": "task_updated", "task_id": "t1", "status": "killed"},
    )

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "killed"
    assert task["completed_at"] is not None
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_mark_interrupted_updates_running_tasks():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())

    await service.mark_interrupted(SESSION_ID, "SDK client 已断开")

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "interrupted"
    assert task["summary"] == "SDK client 已断开"
    assert task["completed_at"] is not None
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_keep_alive_uses_task_start_not_progress():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {"type": "task_progress", "task_id": "t1", "description": "still"},
    )

    deadline = await service.keep_alive_until(SESSION_ID)

    assert deadline == NOW + timedelta(seconds=600)
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_cleanup_removes_completed_tasks_after_retention():
    await init_db()
    now = [NOW]
    service = _service(now=lambda: now[0], retention_seconds=10)
    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {
            "type": "task_notification",
            "task_id": "t1",
            "status": "completed",
            "summary": "done",
        },
    )
    now[0] = NOW + timedelta(seconds=11)

    await service.cleanup_once()

    assert (await service.snapshot(SESSION_ID))["tasks"] == []
    async with SessionLocal() as db:
        assert await db.get(RunningSession, SESSION_ID) is None


@pytest.mark.asyncio
async def test_startup_marks_running_tasks_interrupted():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())

    await service.startup()

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "interrupted"
    assert task["summary"] == "服务重启，任务已中断"
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_subscribe_receives_published_events():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    queue = service.subscribe(SESSION_ID)

    await service.publish(SESSION_ID, _started())

    event = await queue.get()
    assert event["type"] == "task_started"
    assert event["task_id"] == "t1"
    service.unsubscribe(SESSION_ID, queue)
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_cleanup_marks_long_running_tasks_interrupted():
    await init_db()
    await turn_lease.add(SESSION_ID)
    now = [NOW]
    service = _service(now=lambda: now[0], retention_seconds=10)
    await service.publish(SESSION_ID, _started())
    now[0] = NOW + timedelta(seconds=11)

    await service.cleanup_once()

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "interrupted"
    assert task["summary"] == "后台任务超过保活上限"
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_publish_creates_task_only_row():
    await init_db()
    service = _service()

    await service.publish(SESSION_ID, _started())

    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        assert row is not None
        assert row.has_turn is False
        assert json.loads(row.tasks_json)[0]["task_id"] == "t1"
    await turn_lease.remove(SESSION_ID)
