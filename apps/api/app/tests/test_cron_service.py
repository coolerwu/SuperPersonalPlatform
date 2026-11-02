from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, CronJob, Message, Run, Session, utcnow
from app.services.cron_service import (
    CronBusy,
    CronScheduleError,
    next_run,
    run_due_jobs,
    run_job_now,
)


async def _make_job(
    *,
    schedule: str = "0 8 * * *",
    next_run_at: datetime | None = None,
    agent_id: str = "default",
) -> tuple[str, str]:
    async with SessionLocal() as db:
        session = Session(agent_id=agent_id, title="定时 · 测试", origin="cron")
        db.add(session)
        await db.flush()
        job = CronJob(
            agent_id=agent_id,
            name=f"svc-{uuid.uuid4().hex[:10]}",
            prompt="执行测试任务",
            schedule=schedule,
            timezone="Asia/Shanghai",
            enabled=True,
            session_id=session.id,
            next_run_at=next_run_at,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id, session.id


async def _cancel_runs(session_id: str) -> None:
    async with SessionLocal() as db:
        runs = await db.scalars(select(Run).where(Run.session_id == session_id))
        for run in runs:
            run.status = "cancelled"
            run.finished_at = utcnow()
        await db.commit()


def test_next_run_uses_job_timezone() -> None:
    after = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)  # 09:00 in Shanghai
    assert next_run("0 8 * * *", "Asia/Shanghai", after=after) == datetime(
        2026, 11, 3, 0, 0, tzinfo=timezone.utc
    )
    with pytest.raises(CronScheduleError):
        next_run("0 0 31 2 *", "Asia/Shanghai", after=after)
    with pytest.raises(CronScheduleError):
        next_run("0 0 * *", "Asia/Shanghai", after=after)
    with pytest.raises(CronScheduleError):
        next_run("0 8 * * *", "Mars/Olympus", after=after)


async def test_due_job_fires_once_and_catches_up(client) -> None:
    now = utcnow()
    job_id, session_id = await _make_job(next_run_at=now - timedelta(days=2))

    fired = await run_due_jobs(now)
    assert fired

    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.last_run_at is not None
        assert job.next_run_at is not None and job.next_run_at > now
        runs = (
            await db.scalars(select(Run).where(Run.session_id == session_id))
        ).all()
        assert len(runs) == 1
        assert runs[0].source == "cron"
        assert runs[0].status == "queued"
        messages = (
            await db.scalars(select(Message).where(Message.session_id == session_id))
        ).all()
        assert [message.role for message in messages] == ["user"]
        assert messages[0].content == "执行测试任务"

    # A second tick at the same instant must not replay the missed slots.
    await run_due_jobs(now)
    async with SessionLocal() as db:
        runs = (
            await db.scalars(select(Run).where(Run.session_id == session_id))
        ).all()
        assert len(runs) == 1


async def test_due_job_skips_when_session_has_active_run(client) -> None:
    now = utcnow()
    job_id, session_id = await _make_job(next_run_at=now - timedelta(minutes=5))
    async with SessionLocal() as db:
        db.add(
            Run(
                agent_id="default",
                session_id=session_id,
                source="manual",
                status="running",
                input="occupied",
            )
        )
        await db.commit()

    fired = await run_due_jobs(now)
    assert job_id not in {run_id for run_id in fired}

    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.last_skipped_at is not None
        assert job.next_run_at is not None and job.next_run_at > now
        runs = (
            await db.scalars(select(Run).where(Run.session_id == session_id))
        ).all()
        assert len(runs) == 1
        audits = (
            await db.scalars(
                select(AuditLog).where(
                    AuditLog.action == "cron_skipped",
                    AuditLog.entity_id == job_id,
                )
            )
        ).all()
        assert len(audits) == 1

    await _cancel_runs(session_id)
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        job.next_run_at = now - timedelta(minutes=1)
        await db.commit()
    await run_due_jobs(now)
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.last_skipped_at is None
        runs = (
            await db.scalars(select(Run).where(Run.session_id == session_id))
        ).all()
        assert len(runs) == 2


async def test_manual_run_rejects_busy_session(client) -> None:
    now = utcnow()
    job_id, session_id = await _make_job(next_run_at=now + timedelta(days=1))
    async with SessionLocal() as db:
        active = Run(
            agent_id="default",
            session_id=session_id,
            source="manual",
            status="queued",
            input="occupied",
        )
        db.add(active)
        await db.commit()
        await db.refresh(active)

    with pytest.raises(CronBusy):
        await run_job_now(job_id)

    await _cancel_runs(session_id)
    run = await run_job_now(job_id)
    assert run.source == "cron"
    assert run.session_id == session_id


async def test_deleted_session_is_recreated_on_trigger(client) -> None:
    now = utcnow()
    job_id, session_id = await _make_job(next_run_at=now - timedelta(minutes=1))
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        assert session is not None
        await db.delete(session)
        await db.commit()

    await run_due_jobs(now)
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.session_id is not None
        assert job.session_id != session_id
        new_session = await db.get(Session, job.session_id)
        assert new_session is not None
        assert new_session.origin == "cron"
        runs = (
            await db.scalars(select(Run).where(Run.session_id == job.session_id))
        ).all()
        assert len(runs) == 1
