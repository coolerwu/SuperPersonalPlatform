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
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Agent, AuditLog, CronJob, Run, Session, utcnow
from app.services.run_service import ACTIVE_RUN_STATUSES, _create_run_in_db

logger = logging.getLogger(__name__)

SESSION_TITLE_PREFIX = "定时 · "


class CronScheduleError(ValueError):
    """A cron expression or timezone that cannot produce a next occurrence."""


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


async def run_job_now(job_id: str, now: datetime | None = None) -> Run:
    """Manual "run now" entry point; does not change the schedule."""
    async with _job_lock(job_id):
        async with SessionLocal() as db:
            job = await db.get(CronJob, job_id)
            if job is None:
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
