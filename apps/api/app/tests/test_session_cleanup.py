from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Channel,
    ChannelConversation,
    CronJob,
    Message,
    Run,
    RunEvent,
    Session,
    utcnow,
)
from app.services.session_service import (
    SESSION_DELETED,
    SessionCleanupRuntime,
    cleanup_inactive_sessions,
    delete_session_cascade,
)
from app.services.workspace import resolve_workspace_path


async def _make_session(
    *,
    last_message_at: datetime | None = None,
    created_at: datetime | None = None,
) -> str:
    now = utcnow()
    async with SessionLocal() as db:
        session = Session(
            agent_id="default",
            title=f"cleanup-{uuid.uuid4().hex[:8]}",
            created_at=created_at or now,
            updated_at=created_at or now,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        if last_message_at is not None:
            db.add(
                Message(
                    session_id=session.id,
                    role="user",
                    content="hi",
                    created_at=last_message_at,
                )
            )
            await db.commit()
        return session.id


async def test_cleanup_deletes_sessions_idle_beyond_retention(client) -> None:
    now = utcnow()
    old_id = await _make_session(last_message_at=now - timedelta(days=31))
    recent_id = await _make_session(last_message_at=now - timedelta(days=29))
    empty_old_id = await _make_session(created_at=now - timedelta(days=31))

    removed = await cleanup_inactive_sessions(30, now=now)
    assert removed >= 2

    async with SessionLocal() as db:
        assert await db.get(Session, old_id) is None
        assert await db.get(Session, empty_old_id) is None
        assert await db.get(Session, recent_id) is not None


async def test_cleanup_skips_sessions_with_active_run(client) -> None:
    now = utcnow()
    session_id = await _make_session(last_message_at=now - timedelta(days=45))
    async with SessionLocal() as db:
        db.add(
            Run(
                agent_id="default",
                session_id=session_id,
                status="queued",
                input="x",
            )
        )
        await db.commit()

    await cleanup_inactive_sessions(30, now=now)

    async with SessionLocal() as db:
        assert await db.get(Session, session_id) is not None


async def test_cleanup_does_not_starve_behind_active_session(client) -> None:
    now = utcnow()
    older = await _make_session(last_message_at=now - timedelta(days=60))
    newer = await _make_session(last_message_at=now - timedelta(days=40))
    async with SessionLocal() as db:
        db.add(
            Run(
                agent_id="default",
                session_id=older,
                status="running",
                input="x",
            )
        )
        await db.commit()

    removed = await cleanup_inactive_sessions(30, now=now, batch_size=1)

    assert removed == 1
    async with SessionLocal() as db:
        assert await db.get(Session, older) is not None
        assert await db.get(Session, newer) is None


async def test_delete_session_cascade_removes_related_rows_and_files(client) -> None:
    session_id = await _make_session(last_message_at=utcnow())
    async with SessionLocal() as db:
        run = Run(
            agent_id="default",
            session_id=session_id,
            status="completed",
            input="x",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        db.add(RunEvent(run_id=run_id, type="run_completed", payload={}))
        await db.commit()

    checkpoint = settings.checkpoint_dir / f"{session_id}.sqlite"
    checkpoint.write_bytes(b"checkpoint")
    workspace = resolve_workspace_path("default", create=True)
    screenshots = workspace / "screenshots" / session_id
    screenshots.mkdir(parents=True, exist_ok=True)
    (screenshots / "shot.png").write_bytes(b"png")

    async with SessionLocal() as db:
        assert await delete_session_cascade(db, session_id) == SESSION_DELETED

    assert not checkpoint.exists()
    assert not screenshots.exists()
    async with SessionLocal() as db:
        assert await db.get(Session, session_id) is None
        assert (
            await db.scalar(select(Message).where(Message.session_id == session_id))
            is None
        )
        assert await db.scalar(select(Run).where(Run.session_id == session_id)) is None
        assert await db.scalar(select(RunEvent).where(RunEvent.run_id == run_id)) is None


async def test_delete_session_cascade_unlinks_channel_and_cron(client) -> None:
    session_id = await _make_session(last_message_at=utcnow())
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        channel = Channel(
            agent_id="default",
            type="wechat",
            name=f"cleanup-{suffix}",
            status="active",
            account_id=f"acc-{suffix}",
            bot_token="token",
        )
        db.add(channel)
        await db.flush()
        db.add(
            ChannelConversation(
                channel_id=channel.id,
                external_id=f"wx-{suffix}",
                display_name="Alice",
                session_id=session_id,
            )
        )
        job = CronJob(
            agent_id="default",
            name=f"cleanup-{suffix}",
            prompt="p",
            schedule="* * * * *",
            session_id=session_id,
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    async with SessionLocal() as db:
        assert await delete_session_cascade(db, session_id) == SESSION_DELETED

    async with SessionLocal() as db:
        assert (
            await db.scalar(
                select(ChannelConversation).where(
                    ChannelConversation.session_id == session_id
                )
            )
            is None
        )
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.session_id is None


async def test_cleanup_disabled_when_retention_is_zero(client) -> None:
    now = utcnow()
    session_id = await _make_session(last_message_at=now - timedelta(days=90))

    assert await cleanup_inactive_sessions(0, now=now) == 0

    async with SessionLocal() as db:
        assert await db.get(Session, session_id) is not None
        await delete_session_cascade(db, session_id)


async def test_cleanup_runtime_starts_and_stops(client) -> None:
    disabled = SessionCleanupRuntime(retention_days=0)
    await disabled.start()
    assert disabled._task is None
    await disabled.stop()

    runtime = SessionCleanupRuntime(retention_days=30, interval_seconds=60)
    await runtime.start()
    assert runtime._task is not None
    await asyncio.sleep(0.05)
    await runtime.stop()
    assert runtime._task is None
