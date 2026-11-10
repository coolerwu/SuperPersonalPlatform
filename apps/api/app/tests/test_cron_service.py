from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, CronJob, Session, utcnow
from app.services.claude_runner import DeliveryRequest
from app.services.cron_service import (
    CronBusy,
    CronNotFound,
    CronScheduleError,
    create_job,
    detach_session,
    next_run,
    run_due_jobs,
    run_job_now,
    update_job,
)
from app.services.identifiers import is_canonical_uuid
from app.services.settings_store import (
    AgentSection,
    ChannelSection,
    ConversationSection,
    PlatformSettings,
)


class FakeStore:
    """Settings store that hands out copies, like the real JSON-backed one."""

    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings

    async def get(self) -> PlatformSettings:
        return self.settings.model_copy(deep=True)


class FakeRegistry:
    def __init__(self, *, busy: bool = False) -> None:
        self.turns = []
        self.busy = busy
        self.busy_sessions: set[str] = set()

    def is_busy(self, session_id: str) -> bool:
        return self.busy or session_id in self.busy_sessions

    async def submit(self, turn):
        self.turns.append(turn)
        return turn.id


def _settings(
    *,
    agent_status: str = "active",
    channels: list[ChannelSection] | None = None,
    conversations: dict[str, ConversationSection] | None = None,
) -> PlatformSettings:
    return PlatformSettings(
        agents=[AgentSection(id="default", name="Assistant", status=agent_status)],
        channels=list(channels or []),
        channel_conversations=dict(conversations or {}),
    )


def _store(**kwargs) -> FakeStore:
    return FakeStore(_settings(**kwargs))


async def _make_job(
    *,
    prompt: str = "执行测试任务",
    schedule: str = "0 8 * * *",
    next_run_at: datetime | None = None,
    session_id: str | None = None,
    last_run_at: datetime | None = None,
    push_channel_id: str | None = None,
    push_conversation_id: str | None = None,
    agent_id: str = "default",
) -> str:
    async with SessionLocal() as db:
        job = CronJob(
            agent_id=agent_id,
            name=f"svc-{uuid.uuid4().hex[:10]}",
            prompt=prompt,
            schedule=schedule,
            timezone="Asia/Shanghai",
            enabled=True,
            session_id=session_id or str(uuid.uuid4()),
            next_run_at=next_run_at,
            last_run_at=last_run_at,
            push_channel_id=push_channel_id,
            push_conversation_id=push_conversation_id,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


async def _job(job_id: str) -> CronJob:
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        return job


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


async def test_due_job_submits_one_cron_turn(client) -> None:
    now = utcnow()
    session_id = str(uuid.uuid4())
    job_id = await _make_job(
        next_run_at=now - timedelta(days=2), session_id=session_id
    )
    store, registry = _store(), FakeRegistry()

    fired = await run_due_jobs(now, store=store, registry=registry)
    assert job_id in fired
    submitted = [
        turn for turn in registry.turns if turn.session_id == session_id
    ]
    assert len(submitted) == 1
    turn = submitted[0]
    assert turn.source == "cron"
    assert turn.agent_id == "default"
    assert turn.prompt == "执行测试任务"
    assert turn.delivery is None

    job = await _job(job_id)
    assert job.last_run_at is not None
    assert job.next_run_at is not None and job.next_run_at > now
    assert job.last_skipped_at is None

    # A second tick at the same instant must not replay the missed slots.
    await run_due_jobs(now, store=store, registry=registry)
    assert (
        len([turn for turn in registry.turns if turn.session_id == session_id]) == 1
    )


async def test_busy_job_is_skipped_and_audited(client) -> None:
    now = utcnow()
    session_id = str(uuid.uuid4())
    job_id = await _make_job(
        next_run_at=now - timedelta(minutes=5), session_id=session_id
    )
    store, registry = _store(), FakeRegistry()
    registry.busy_sessions.add(session_id)

    fired = await run_due_jobs(now, store=store, registry=registry)
    assert job_id not in fired
    assert [turn for turn in registry.turns if turn.session_id == session_id] == []

    job = await _job(job_id)
    assert job.last_skipped_at is not None
    assert job.last_run_at is None
    assert job.next_run_at is not None and job.next_run_at > now
    async with SessionLocal() as db:
        audits = (
            await db.scalars(
                select(AuditLog).where(
                    AuditLog.action == "cron_skipped",
                    AuditLog.entity_id == job_id,
                )
            )
        ).all()
        assert len(audits) == 1


async def test_manual_run_rejects_busy_session(client) -> None:
    now = utcnow()
    session_id = str(uuid.uuid4())
    job_id = await _make_job(
        next_run_at=now + timedelta(days=1), session_id=session_id
    )
    store, registry = _store(), FakeRegistry()
    registry.busy_sessions.add(session_id)

    with pytest.raises(CronBusy):
        await run_job_now(job_id, store=store, registry=registry)
    assert registry.turns == []


async def test_manual_run_submits_without_changing_schedule(client) -> None:
    now = utcnow()
    upcoming = now + timedelta(days=1)
    session_id = str(uuid.uuid4())
    job_id = await _make_job(next_run_at=upcoming, session_id=session_id)
    store, registry = _store(), FakeRegistry()

    job = await run_job_now(job_id, store=store, registry=registry)
    assert job.session_id == session_id
    assert job.last_run_at is not None
    assert job.next_run_at == upcoming
    assert len(registry.turns) == 1
    assert registry.turns[0].source == "cron"
    assert registry.turns[0].session_id == session_id


async def test_missing_transcript_regenerates_session_id(client) -> None:
    now = utcnow()
    old_session_id = str(uuid.uuid4())
    job_id = await _make_job(
        next_run_at=now - timedelta(minutes=1),
        session_id=old_session_id,
        last_run_at=now - timedelta(hours=1),
    )
    store, registry = _store(), FakeRegistry()

    fired = await run_due_jobs(now, store=store, registry=registry)
    assert job_id in fired

    job = await _job(job_id)
    assert job.session_id is not None
    assert job.session_id != old_session_id
    assert is_canonical_uuid(job.session_id)
    submitted = [
        turn for turn in registry.turns if turn.session_id == job.session_id
    ]
    assert len(submitted) == 1


async def test_non_uuid_session_id_is_replaced(client) -> None:
    now = utcnow()
    job_id = await _make_job(
        next_run_at=now + timedelta(days=1), session_id="legacy-session"
    )
    store, registry = _store(), FakeRegistry()

    await run_job_now(job_id, store=store, registry=registry)
    job = await _job(job_id)
    assert is_canonical_uuid(job.session_id)
    assert registry.turns[0].session_id == job.session_id


async def test_busy_missing_transcript_keeps_session_id(client) -> None:
    """A running turn may not have written its transcript yet; keep its id."""
    now = utcnow()
    session_id = str(uuid.uuid4())
    job_id = await _make_job(
        next_run_at=now + timedelta(days=1),
        session_id=session_id,
        last_run_at=now - timedelta(seconds=1),
    )
    store, registry = _store(), FakeRegistry()
    registry.busy_sessions.add(session_id)

    with pytest.raises(CronBusy):
        await run_job_now(job_id, store=store, registry=registry)
    job = await _job(job_id)
    assert job.session_id == session_id
    assert registry.turns == []


async def test_push_target_creates_delivery(client) -> None:
    now = utcnow()
    job_id = await _make_job(
        next_run_at=now - timedelta(minutes=1),
        push_channel_id="ch1",
        push_conversation_id="wx-1",
    )
    store = _store(
        channels=[
            ChannelSection(
                id="ch1", agent_id="default", type="wechat", name="微信"
            )
        ],
        conversations={
            "ch1:wx-1": ConversationSection(
                session_id=str(uuid.uuid4()),
                channel_id="ch1",
                display_name="Alice",
            )
        },
    )
    registry = FakeRegistry()

    fired = await run_due_jobs(now, store=store, registry=registry)
    assert job_id in fired
    submitted = [
        turn
        for turn in registry.turns
        if turn.delivery is not None
    ]
    assert len(submitted) == 1
    assert submitted[0].delivery == DeliveryRequest(
        channel_id="ch1", external_id="wx-1"
    )


async def test_unavailable_push_target_submits_without_delivery(client) -> None:
    now = utcnow()
    missing_conversation = await _make_job(
        next_run_at=now - timedelta(minutes=1),
        push_channel_id="ch1",
        push_conversation_id="wx-missing",
    )
    foreign_channel = await _make_job(
        next_run_at=now - timedelta(minutes=1),
        push_channel_id="ch2",
        push_conversation_id="wx-2",
    )
    store = _store(
        channels=[
            ChannelSection(
                id="ch1", agent_id="default", type="wechat", name="微信"
            ),
            ChannelSection(
                id="ch2", agent_id="other", type="wechat", name="别人的微信"
            ),
        ],
        conversations={
            "ch2:wx-2": ConversationSection(
                session_id=str(uuid.uuid4()), channel_id="ch2"
            )
        },
    )
    registry = FakeRegistry()

    fired = await run_due_jobs(now, store=store, registry=registry)
    assert {missing_conversation, foreign_channel} <= set(fired)
    produced = [
        turn
        for turn in registry.turns
        if turn.session_id
        in {
            (await _job(missing_conversation)).session_id,
            (await _job(foreign_channel)).session_id,
        }
    ]
    assert len(produced) == 2
    assert [turn.delivery for turn in produced] == [None, None]


async def test_inactive_agent_advances_without_turn(client) -> None:
    now = utcnow()
    job_id = await _make_job(next_run_at=now - timedelta(minutes=1))
    store, registry = _store(agent_status="paused"), FakeRegistry()

    fired = await run_due_jobs(now, store=store, registry=registry)
    assert job_id not in fired
    job = await _job(job_id)
    assert [turn for turn in registry.turns if turn.session_id == job.session_id] == []

    assert job.last_run_at is None
    assert job.next_run_at is not None and job.next_run_at > now


async def test_inactive_agent_manual_run_fails(client) -> None:
    now = utcnow()
    job_id = await _make_job(next_run_at=now + timedelta(days=1))
    store, registry = _store(agent_status="archived"), FakeRegistry()

    from app.services.cron_service import CronInactiveAgent

    with pytest.raises(CronInactiveAgent):
        await run_job_now(job_id, store=store, registry=registry)
    assert registry.turns == []


async def test_create_job_uses_transcript_session_id(client) -> None:
    name = f"新建-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as db:
        job = await create_job(
            db,
            store=_store(),
            agent_id="default",
            name=name,
            prompt="执行新建任务",
            schedule="0 8 * * *",
        )
        await db.commit()
        await db.refresh(job)
        assert is_canonical_uuid(job.session_id)
        assert await db.get(Session, job.session_id) is None


async def test_create_job_requires_active_settings_agent(client) -> None:
    async with SessionLocal() as db:
        with pytest.raises(CronNotFound):
            await create_job(
                db,
                store=_store(agent_status="paused"),
                agent_id="default",
                name=f"暂停-{uuid.uuid4().hex[:8]}",
                prompt="不应该创建",
                schedule="0 8 * * *",
            )
        with pytest.raises(CronNotFound):
            await create_job(
                db,
                store=_store(),
                agent_id="missing-agent",
                name=f"缺失-{uuid.uuid4().hex[:8]}",
                prompt="不应该创建",
                schedule="0 8 * * *",
            )


async def test_update_job_sets_and_clears_push_target(client) -> None:
    store = _store(
        channels=[
            ChannelSection(
                id="ch1", agent_id="default", type="wechat", name="微信"
            )
        ],
        conversations={
            "ch1:wx-1": ConversationSection(
                session_id=str(uuid.uuid4()),
                channel_id="ch1",
                display_name="Alice",
            )
        },
    )
    async with SessionLocal() as db:
        job = await create_job(
            db,
            store=store,
            agent_id="default",
            name=f"推送-{uuid.uuid4().hex[:8]}",
            prompt="检查推送目标",
            schedule="0 8 * * *",
        )
        await db.commit()
        await db.refresh(job)

        job = await update_job(
            db,
            job.id,
            store=store,
            agent_id="default",
            push_channel_id="ch1",
            push_conversation_id="wx-1",
        )
        assert job.push_channel_id == "ch1"
        assert job.push_conversation_id == "wx-1"

        job = await update_job(
            db,
            job.id,
            store=store,
            agent_id="default",
            push_channel_id="",
            push_conversation_id="",
        )
        assert job.push_channel_id is None
        assert job.push_conversation_id is None
        await db.commit()


async def test_detach_session_is_a_noop(client) -> None:
    job_id = await _make_job()
    session_id = (await _job(job_id)).session_id
    assert session_id is not None
    async with SessionLocal() as db:
        await detach_session(db, session_id)
        await db.commit()
    assert (await _job(job_id)).session_id == session_id
