import asyncio
import json
import logging
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


@pytest.fixture(autouse=True)
async def _clean_session():
    await init_db()
    await turn_lease.remove(SESSION_ID)
    yield
    await turn_lease.remove(SESSION_ID)


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
    now = [NOW]
    service = _service(now=lambda: now[0])
    await service.publish(SESSION_ID, _started())
    now[0] = NOW + timedelta(minutes=5)
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


@pytest.mark.asyncio
async def test_progress_after_terminal_is_ignored():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())
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

    await service.publish(
        SESSION_ID,
        {
            "type": "task_progress",
            "task_id": "t1",
            "description": "不应覆盖",
            "last_tool_name": "Bash",
            "usage": {"tool_uses": 9},
        },
    )

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "completed"
    assert task["summary"] == "完成"
    assert task["last_tool_name"] == ""
    assert task["usage"] == {"tool_uses": 3}
    await turn_lease.remove(SESSION_ID)


@pytest.mark.parametrize("status", ["failed", "stopped"])
@pytest.mark.asyncio
async def test_notification_terminal_statuses_set_completed_at(status):
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {
            "type": "task_notification",
            "task_id": "t1",
            "status": status,
            "summary": "end",
        },
    )

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == status
    assert task["completed_at"] is not None
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_task_limit_drops_oldest_terminal_and_emits_removed():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    queue = service.subscribe(SESSION_ID)

    for index in range(33):
        await service.publish(
            SESSION_ID,
            {
                "type": "task_notification",
                "task_id": f"t{index}",
                "status": "completed",
                "summary": "done",
            },
        )
    await service.publish(SESSION_ID, _started(task_id="running"))

    snapshot = await service.snapshot(SESSION_ID)
    ids = {task["task_id"] for task in snapshot["tasks"]}
    assert len(ids) == 32
    assert "running" in ids

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    removed = [
        event["task_id"] for event in events if event["type"] == "task_removed"
    ]
    assert removed == ["t0", "t1"]
    assert "running" not in removed
    assert ids.isdisjoint(removed)
    service.unsubscribe(SESSION_ID, queue)
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_json_limit_drops_terminal_tasks_and_keeps_running():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started(task_id="running"))
    for index in range(4):
        await service.publish(
            SESSION_ID,
            {
                "type": "task_notification",
                "task_id": f"t{index}",
                "status": "completed",
                "summary": "x" * 30_000 + f"-{index}",
            },
        )

    snapshot = await service.snapshot(SESSION_ID)
    ids = {task["task_id"] for task in snapshot["tasks"]}
    assert "running" in ids
    assert len(ids) == 3
    assert len(json.dumps(snapshot["tasks"]).encode("utf-8")) <= 64 * 1024
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_cleanup_keeps_turn_row():
    await init_db()
    await turn_lease.add(SESSION_ID)
    now = [NOW]
    service = _service(now=lambda: now[0], retention_seconds=10)
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
        row = await db.get(RunningSession, SESSION_ID)
        assert row is not None
        assert row.has_turn is True
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_forget_closes_subscribers_and_deletes_row():
    await init_db()
    service = _service()
    await service.publish(SESSION_ID, _started())
    queue = service.subscribe(SESSION_ID)

    await service.forget(SESSION_ID)

    assert await queue.get() is None
    async with SessionLocal() as db:
        assert await db.get(RunningSession, SESSION_ID) is None


@pytest.mark.asyncio
async def test_unsubscribe_leaves_other_subscribers():
    await init_db()
    service = _service()
    first = service.subscribe(SESSION_ID)
    second = service.subscribe(SESSION_ID)

    await service.publish(SESSION_ID, _started())
    assert (await first.get())["type"] == "task_started"
    assert (await second.get())["type"] == "task_started"

    service.unsubscribe(SESSION_ID, first)
    await service.publish(
        SESSION_ID,
        {"type": "task_updated", "task_id": "t1", "status": "completed"},
    )

    assert (await second.get())["type"] == "task_updated"
    assert first.empty()
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_publish_persist_failure_is_best_effort(monkeypatch):
    await init_db()
    service = _service()

    async def _boom(session_id, tasks):
        raise RuntimeError("write failed")

    monkeypatch.setattr(service, "_persist", _boom)

    await service.publish(SESSION_ID, _started())

    snapshot = await service.snapshot(SESSION_ID)
    assert snapshot["tasks"][0]["task_id"] == "t1"
    assert SESSION_ID in service._dirty
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_stop_flushes_dirty_progress():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {"type": "task_progress", "task_id": "t1", "description": "progress"},
    )
    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        assert json.loads(row.tasks_json)[0]["description"] == "回测 BTC 1h"

    await service.stop()

    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        assert json.loads(row.tasks_json)[0]["description"] == "progress"
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_snapshot_ignores_malformed_tasks_json(caplog):
    await init_db()
    async with SessionLocal() as db:
        db.add(
            RunningSession(
                session_id=SESSION_ID,
                has_turn=False,
                tasks_json="{bad",
            )
        )
        await db.commit()
    service = _service()

    with caplog.at_level(logging.WARNING):
        snapshot = await service.snapshot(SESSION_ID)

    assert snapshot["tasks"] == []
    assert "malformed tasks_json" in caplog.text
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_task_updated_cannot_downgrade_terminal_status():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())
    await service.publish(
        SESSION_ID,
        {
            "type": "task_notification",
            "task_id": "t1",
            "status": "completed",
            "summary": "完成",
        },
    )

    await service.publish(
        SESSION_ID,
        {"type": "task_updated", "task_id": "t1", "status": "running"},
    )

    task = (await service.snapshot(SESSION_ID))["tasks"][0]
    assert task["status"] == "completed"
    assert task["summary"] == "完成"
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_snapshot_filters_expired_terminal_tasks():
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

    snapshot = await service.snapshot(SESSION_ID)

    assert snapshot["tasks"] == []
    async with SessionLocal() as db:
        assert await db.get(RunningSession, SESSION_ID) is not None


@pytest.mark.asyncio
async def test_forget_keeps_active_turn_lease(caplog):
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.publish(SESSION_ID, _started())
    queue = service.subscribe(SESSION_ID)

    with caplog.at_level(logging.WARNING):
        await service.forget(SESSION_ID)

    assert queue.empty()
    assert SESSION_ID in service._subscribers
    assert SESSION_ID in service._live
    async with SessionLocal() as db:
        row = await db.get(RunningSession, SESSION_ID)
        assert row is not None
        assert row.has_turn is True
    assert "refusing to forget active turn lease" in caplog.text
    await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_service_can_restart():
    await init_db()
    service = _service()

    await service.start()
    assert service._writer is not None
    assert service._cleaner is not None

    await service.stop()
    assert service._writer is None
    assert service._cleaner is None

    await service.start()
    assert service._writer is not None
    await service.stop()
    assert service._writer is None


@pytest.mark.asyncio
async def test_writer_loop_persists_progress():
    await init_db()
    await turn_lease.add(SESSION_ID)
    service = _service()
    await service.start()
    try:
        await service.publish(SESSION_ID, _started())
        await service.publish(
            SESSION_ID,
            {
                "type": "task_progress",
                "task_id": "t1",
                "description": "progress",
            },
        )
        deadline = asyncio.get_running_loop().time() + 2.0
        while True:
            async with SessionLocal() as db:
                row = await db.get(RunningSession, SESSION_ID)
                if (
                    row is not None
                    and json.loads(row.tasks_json)[0]["description"] == "progress"
                ):
                    break
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("writer loop did not persist progress in time")
            await asyncio.sleep(0.05)
    finally:
        await service.stop()
        await turn_lease.remove(SESSION_ID)


@pytest.mark.asyncio
async def test_cleanup_prunes_empty_live_entry():
    await init_db()
    service = _service()
    await service.snapshot(SESSION_ID)
    assert SESSION_ID in service._live

    await service.cleanup_once()

    assert SESSION_ID not in service._live


@pytest.mark.asyncio
async def test_cleanup_keeps_live_entry_with_subscriber():
    await init_db()
    service = _service()
    await service.snapshot(SESSION_ID)
    queue = service.subscribe(SESSION_ID)

    await service.cleanup_once()

    assert SESSION_ID in service._live
    service.unsubscribe(SESSION_ID, queue)


@pytest.mark.asyncio
async def test_cleanup_keeps_dirty_live_entry():
    await init_db()
    service = _service()
    await service.snapshot(SESSION_ID)
    service._dirty.add(SESSION_ID)

    await service.cleanup_once()

    assert SESSION_ID in service._live
