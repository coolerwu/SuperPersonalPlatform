"""Scheduled prompts (cron) attached to an Agent's dedicated Session.

The scheduler runs in the API process and only writes rows: a due job creates a
normal user Message + queued Run, then updates its next occurrence. Execution,
streaming, retries and restart recovery stay in the existing RunWorker.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, croniter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Agent,
    AuditLog,
    Channel,
    ChannelConversation,
    ChannelRunLink,
    CronJob,
    Run,
    Session,
    utcnow,
)
from app.services.run_service import ACTIVE_RUN_STATUSES, _create_run_in_db

logger = logging.getLogger(__name__)

SESSION_TITLE_PREFIX = "定时 · "


class CronValidationError(ValueError):
    """Invalid user input for a cron job."""


class CronScheduleError(CronValidationError):
    """A cron expression or timezone that cannot produce a next occurrence."""


class CronConflict(RuntimeError):
    """The Agent already has a job with this name."""


class CronNotFound(LookupError):
    """The job does not exist or belongs to another Agent."""


class CronBusy(Exception):
    def __init__(self, run_id: str, status: str) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(f"session already has active run {run_id} ({status})")


class CronInactiveAgent(Exception):
    pass


def session_title(name: str) -> str:
    return f"{SESSION_TITLE_PREFIX}{name}".strip()


def normalize_schedule(value: str) -> str:
    schedule = " ".join(str(value or "").split())
    if len(schedule.split(" ")) != 5:
        raise CronScheduleError("cron 表达式需要 5 个字段：分 时 日 月 周")
    if not croniter.is_valid(schedule):
        raise CronScheduleError("cron 表达式无效")
    return schedule


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronScheduleError(
            "时区无效，请使用 IANA 时区名称，例如 Asia/Shanghai"
        ) from exc


def validate_timezone(value: str) -> str:
    name = str(value or "").strip() or settings.cron_default_timezone
    _zone(name)
    return name


def next_run(schedule: str, tz_name: str, after: datetime | None = None) -> datetime:
    """Return the next occurrence strictly after ``after`` (an aware UTC time)."""
    moment = after or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone = _zone(tz_name)
    local = moment.astimezone(zone)
    try:
        nxt = croniter(normalize_schedule(schedule), local).get_next(datetime)
    except CroniterBadDateError as exc:
        raise CronScheduleError("cron 表达式无法计算出下一次触发时间") from exc
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=zone)
    return nxt.astimezone(timezone.utc)


def _advance(job: CronJob, now: datetime) -> None:
    try:
        job.next_run_at = next_run(job.schedule, job.timezone, after=now)
    except CronScheduleError:
        logger.warning(
            "cron job %s has no future occurrence; disabling it", job.id
        )
        job.enabled = False
        job.next_run_at = None


MAX_NAME_LENGTH = 120


def clean_name(raw: str) -> str:
    name = str(raw or "").strip()
    if not name:
        raise CronValidationError("任务名称不能为空")
    if len(name) > MAX_NAME_LENGTH:
        raise CronValidationError(f"任务名称不能超过 {MAX_NAME_LENGTH} 个字符")
    return name


def clean_prompt(raw: str) -> str:
    prompt = str(raw or "").strip()
    if not prompt:
        raise CronValidationError("任务内容不能为空")
    return prompt


def _compute_next(job: CronJob, upcoming: datetime | None = None) -> None:
    # Always compute once so an expression with no future occurrence is
    # rejected even while the job is paused.
    upcoming = upcoming or next_run(job.schedule, job.timezone)
    job.next_run_at = upcoming if job.enabled else None


async def _ensure_unique_name(
    db: AsyncSession, agent_id: str, name: str, exclude_id: str | None = None
) -> None:
    statement = select(CronJob.id).where(
        CronJob.agent_id == agent_id, CronJob.name == name
    )
    if exclude_id is not None:
        statement = statement.where(CronJob.id != exclude_id)
    if await db.scalar(statement) is not None:
        raise CronConflict("同一 Agent 下已存在同名定时任务")


async def resolve_push_target(
    db: AsyncSession,
    agent_id: str,
    channel_id: str | None,
    conversation_id: str | None,
) -> tuple[Channel | None, ChannelConversation | None]:
    """Validate an explicit WeChat 1:1 delivery target."""
    channel_id = str(channel_id or "").strip()
    conversation_id = str(conversation_id or "").strip()
    if not channel_id and not conversation_id:
        return None, None
    if not channel_id or not conversation_id:
        raise CronValidationError("推送目标需要同时指定渠道和联系人")
    channel = await db.get(Channel, channel_id)
    if channel is None or channel.type != "wechat" or channel.agent_id != agent_id:
        raise CronValidationError("推送渠道不存在或不属于当前 Agent")
    conversation = await db.get(ChannelConversation, conversation_id)
    if conversation is None or conversation.channel_id != channel.id:
        raise CronValidationError("推送联系人不存在或不属于该渠道")
    return channel, conversation


async def list_jobs(db: AsyncSession, agent_id: str) -> list[CronJob]:
    return list(
        (
            await db.scalars(
                select(CronJob)
                .where(CronJob.agent_id == agent_id)
                .order_by(CronJob.created_at)
            )
        ).all()
    )


async def get_job(
    db: AsyncSession, job_id: str, *, agent_id: str | None = None
) -> CronJob | None:
    statement = select(CronJob).where(CronJob.id == job_id)
    if agent_id is not None:
        statement = statement.where(CronJob.agent_id == agent_id)
    return await db.scalar(statement)


async def create_job(
    db: AsyncSession,
    *,
    agent_id: str,
    name: str,
    prompt: str,
    schedule: str,
    timezone: str = "",
    enabled: bool = True,
    push_channel_id: str = "",
    push_conversation_id: str = "",
) -> CronJob:
    """Validate and add a job + its dedicated Session to the caller's transaction."""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.status == "archived":
        raise CronNotFound("agent not found")

    name = clean_name(name)
    prompt = clean_prompt(prompt)
    schedule = normalize_schedule(schedule)
    tz_name = validate_timezone(timezone)
    upcoming = next_run(schedule, tz_name)
    await _ensure_unique_name(db, agent.id, name)
    push_channel, push_conversation = await resolve_push_target(
        db, agent.id, push_channel_id, push_conversation_id
    )

    session = Session(agent_id=agent.id, title=session_title(name), origin="cron")
    db.add(session)
    await db.flush()

    job = CronJob(
        agent_id=agent.id,
        name=name,
        prompt=prompt,
        schedule=schedule,
        timezone=tz_name,
        enabled=bool(enabled),
        session_id=session.id,
        push_channel_id=push_channel.id if push_channel else None,
        push_conversation_id=push_conversation.id if push_conversation else None,
    )
    _compute_next(job, upcoming)
    db.add(job)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise CronConflict("同一 Agent 下已存在同名定时任务") from exc
    return job


async def update_job(
    db: AsyncSession,
    job_id: str,
    *,
    agent_id: str,
    name: str | None = None,
    prompt: str | None = None,
    schedule: str | None = None,
    timezone: str | None = None,
    enabled: bool | None = None,
    push_channel_id: str | None = None,
    push_conversation_id: str | None = None,
) -> CronJob:
    job = await get_job(db, job_id, agent_id=agent_id)
    if job is None:
        raise CronNotFound("cron job not found")

    schedule_changed = False
    if name is not None:
        new_name = clean_name(name)
        await _ensure_unique_name(db, job.agent_id, new_name, exclude_id=job.id)
        if new_name != job.name:
            job.name = new_name
            schedule_changed = True
            session = await db.get(Session, job.session_id) if job.session_id else None
            if session is not None:
                session.title = session_title(new_name)
    if prompt is not None:
        job.prompt = clean_prompt(prompt)
    if schedule is not None:
        new_schedule = normalize_schedule(schedule)
        if new_schedule != job.schedule:
            job.schedule = new_schedule
            schedule_changed = True
    if timezone is not None:
        new_timezone = validate_timezone(timezone)
        if new_timezone != job.timezone:
            job.timezone = new_timezone
            schedule_changed = True
    if enabled is not None:
        new_enabled = bool(enabled)
        if new_enabled != job.enabled:
            job.enabled = new_enabled
            schedule_changed = True

    if push_channel_id is not None or push_conversation_id is not None:
        if push_channel_id is None or push_conversation_id is None:
            raise CronValidationError("推送目标需要同时指定渠道和联系人")
        channel, conversation = await resolve_push_target(
            db, job.agent_id, push_channel_id, push_conversation_id
        )
        job.push_channel_id = channel.id if channel else None
        job.push_conversation_id = conversation.id if conversation else None

    if schedule_changed or (job.enabled and job.next_run_at is None):
        _compute_next(job)
    elif not job.enabled:
        job.next_run_at = None

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise CronConflict("同一 Agent 下已存在同名定时任务") from exc
    return job


async def delete_job(db: AsyncSession, job_id: str, *, agent_id: str) -> CronJob:
    job = await get_job(db, job_id, agent_id=agent_id)
    if job is None:
        raise CronNotFound("cron job not found")
    await db.delete(job)
    await db.flush()
    return job


async def _link_push_target(db: AsyncSession, job: CronJob, run: Run) -> None:
    """Attach the run to the job's explicit WeChat conversation, if configured."""
    if not job.push_channel_id or not job.push_conversation_id:
        return
    channel = await db.get(Channel, job.push_channel_id)
    conversation = await db.get(ChannelConversation, job.push_conversation_id)
    if (
        channel is None
        or channel.type != "wechat"
        or channel.agent_id != job.agent_id
        or conversation is None
        or conversation.channel_id != channel.id
    ):
        logger.warning(
            "cron job %s push target is unavailable; run will not be delivered",
            job.id,
        )
        return
    db.add(
        ChannelRunLink(
            run_id=run.id,
            channel_id=channel.id,
            conversation_id=conversation.id,
        )
    )


async def trigger_job(
    db: AsyncSession,
    job: CronJob,
    *,
    now: datetime | None = None,
    immediate: bool = False,
) -> Run | None:
    """Create one Run for ``job`` inside the caller's transaction.

    Scheduled triggers skip with an audit row when the session already has an
    active Run; manual triggers raise :class:`CronBusy` instead.
    """
    moment = now or utcnow()
    agent = await db.get(Agent, job.agent_id)
    if agent is None or agent.status != "active":
        if not immediate:
            _advance(job, moment)
        return None

    session = await db.get(Session, job.session_id) if job.session_id else None
    if session is None or session.agent_id != job.agent_id:
        session = Session(
            agent_id=job.agent_id,
            title=session_title(job.name),
            origin="cron",
        )
        db.add(session)
        await db.flush()
        job.session_id = session.id

    active = await db.scalar(
        select(Run)
        .where(
            Run.session_id == session.id,
            Run.status.in_(ACTIVE_RUN_STATUSES),
        )
        .limit(1)
    )
    if active is not None:
        job.last_skipped_at = moment
        db.add(
            AuditLog(
                actor="system",
                action="cron_skipped",
                entity="cron_job",
                entity_id=job.id,
            )
        )
        if immediate:
            raise CronBusy(active.id, active.status)
        _advance(job, moment)
        return None

    run, _message = await _create_run_in_db(db, session, job.prompt, source="cron")
    await _link_push_target(db, job, run)
    job.last_run_at = moment
    job.last_run_id = run.id
    job.last_skipped_at = None
    if not immediate:
        _advance(job, moment)
    return run


_locks: dict[str, asyncio.Lock] = {}


def _job_lock(job_id: str) -> asyncio.Lock:
    lock = _locks.get(job_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[job_id] = lock
    return lock


async def run_due_jobs(now: datetime | None = None) -> list[str]:
    """Fire every enabled job whose next occurrence has passed.

    Each job fires at most once per call: the next occurrence is recomputed
    from ``now``, so a long outage cannot replay all missed slots.
    """
    moment = now or utcnow()
    async with SessionLocal() as db:
        job_ids = list(
            (
                await db.scalars(
                    select(CronJob.id).where(
                        CronJob.enabled.is_(True),
                        CronJob.next_run_at.is_not(None),
                        CronJob.next_run_at <= moment,
                    )
                )
            ).all()
        )

    fired: list[str] = []
    for job_id in job_ids:
        async with _job_lock(job_id):
            async with SessionLocal() as db:
                job = await db.get(CronJob, job_id)
                if (
                    job is None
                    or not job.enabled
                    or job.next_run_at is None
                    or job.next_run_at > moment
                ):
                    continue
                try:
                    run = await trigger_job(db, job, now=moment)
                    await db.commit()
                except Exception:
                    await db.rollback()
                    logger.exception("cron job %s failed to trigger", job_id)
                    continue
                if run is not None:
                    fired.append(run.id)
    return fired


async def run_job_now(
    job_id: str, now: datetime | None = None, *, agent_id: str | None = None
) -> Run:
    """Manual "run now" entry point; does not change the schedule."""
    async with _job_lock(job_id):
        async with SessionLocal() as db:
            job = await db.get(CronJob, job_id)
            if job is None or (agent_id is not None and job.agent_id != agent_id):
                raise LookupError("cron job not found")
            agent = await db.get(Agent, job.agent_id)
            if agent is None or agent.status != "active":
                raise CronInactiveAgent("agent is archived or paused")
            run = await trigger_job(db, job, now=now, immediate=True)
            if run is None:
                raise CronInactiveAgent("agent is archived or paused")
            await db.commit()
            await db.refresh(run)
            return run


async def detach_session(db: AsyncSession, session_id: str) -> None:
    """Clear the job's session binding after the session itself is deleted."""
    jobs = (
        await db.scalars(select(CronJob).where(CronJob.session_id == session_id))
    ).all()
    for job in jobs:
        job.session_id = None
        job.last_run_id = None


class CronRuntime:
    def __init__(self, worker=None, tick_seconds: int | None = None) -> None:  # noqa: ANN001
        self.worker = worker
        self.tick_seconds = max(
            1, tick_seconds if tick_seconds is not None else settings.cron_tick_seconds
        )
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="cron-runtime")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                fired = await run_due_jobs()
                if fired and self.worker is not None:
                    self.worker.wake()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cron tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
