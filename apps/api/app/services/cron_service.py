"""Scheduled prompts (cron) bound to a stable Claude SDK session.

The scheduler runs in the API process and submits turns to ``SessionRegistry``.
A job owns a session id (not a ``sessions`` row) so every trigger resumes the
same SDK transcript and manual follow-ups in that transcript share context.
Agent configuration, channel push targets and conversation bindings come from
the settings store, the same source the rest of the SDK runtime uses.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, croniter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, CronJob, utcnow
from app.services.claude_runner import DeliveryRequest, Turn, transcript_path
from app.services.identifiers import is_canonical_uuid

logger = logging.getLogger(__name__)


class CronValidationError(ValueError):
    """Invalid user input for a cron job."""


class CronScheduleError(CronValidationError):
    """A cron expression or timezone that cannot produce a next occurrence."""


class CronConflict(RuntimeError):
    """The Agent already has a job with this name."""


class CronNotFound(LookupError):
    """The job does not exist or belongs to another Agent."""


class CronBusy(Exception):
    def __init__(self, session_id: str, status: str) -> None:
        self.session_id = session_id
        self.status = status
        super().__init__(f"session is busy ({status})")


INACTIVE_AGENT_MESSAGES = {
    "paused": "Agent 已暂停，无法创建或运行定时任务",
    "archived": "Agent 已归档，无法运行定时任务",
    "missing": "Agent 不存在",
    "inactive": "Agent 不可用",
}


def inactive_agent_message(status: str) -> str:
    """User-visible text for an Agent that cannot create or run cron jobs."""
    return INACTIVE_AGENT_MESSAGES.get(
        status, f"Agent 状态为 {status}，无法运行定时任务"
    )


class CronInactiveAgent(Exception):
    """The Agent cannot run a turn; ``status`` is paused, archived or missing."""

    def __init__(self, status: str = "inactive") -> None:
        self.status = status
        super().__init__(inactive_agent_message(status))


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


def _normalize_push_target(
    channel_id: str | None, conversation_id: str | None
) -> tuple[str | None, str | None]:
    """Normalize a push target and require the two halves to be paired."""
    channel = str(channel_id or "").strip() or None
    conversation = str(conversation_id or "").strip() or None
    if bool(channel) != bool(conversation):
        raise CronValidationError("推送目标需要同时指定渠道和联系人")
    return channel, conversation


async def resolve_push_target(
    store: Any,
    agent_id: str,
    channel_id: str | None,
    conversation_id: str | None,
) -> tuple[str | None, str | None]:
    """Validate an explicit WeChat 1:1 delivery target against settings.

    ``conversation_id`` is the contact's external id, not a database row id.
    The returned values are safe to persist on the job.
    """
    channel, conversation = _normalize_push_target(channel_id, conversation_id)
    if channel is None:
        return None, None
    snapshot = await store.get()
    section = next((item for item in snapshot.channels if item.id == channel), None)
    if (
        section is None
        or section.type != "wechat"
        or section.agent_id != agent_id
    ):
        raise CronValidationError("推送渠道不存在或不属于当前 Agent")
    if section.status != "active":
        raise CronValidationError("推送渠道未启用")
    binding = snapshot.channel_conversations.get(f"{channel}:{conversation}")
    if binding is None or binding.channel_id != channel:
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
    store: Any,
    agent_id: str,
    name: str,
    prompt: str,
    schedule: str,
    timezone: str = "",
    enabled: bool = True,
    push_channel_id: str = "",
    push_conversation_id: str = "",
) -> CronJob:
    """Validate and add a job to the caller's transaction.

    The Agent must be active in the current settings snapshot, the same source
    the router and runtime use. The job starts with a freshly generated session
    id (an SDK transcript identity) so its first trigger resumes nothing yet
    but later triggers and user follow-ups all share that transcript.
    """
    snapshot = await store.get()
    agent = next((item for item in snapshot.agents if item.id == agent_id), None)
    if agent is None:
        raise CronNotFound("agent not found")
    if agent.status != "active":
        raise CronInactiveAgent(agent.status)

    name = clean_name(name)
    prompt = clean_prompt(prompt)
    schedule = normalize_schedule(schedule)
    tz_name = validate_timezone(timezone)
    upcoming = next_run(schedule, tz_name)
    await _ensure_unique_name(db, agent.id, name)
    push_channel, push_conversation = await resolve_push_target(
        store, agent.id, push_channel_id, push_conversation_id
    )

    job = CronJob(
        agent_id=agent.id,
        name=name,
        prompt=prompt,
        schedule=schedule,
        timezone=tz_name,
        enabled=bool(enabled),
        session_id=str(uuid.uuid4()),
        push_channel_id=push_channel,
        push_conversation_id=push_conversation,
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
    store: Any,
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
        push_channel, push_conversation = await resolve_push_target(
            store, job.agent_id, push_channel_id, push_conversation_id
        )
        job.push_channel_id = push_channel
        job.push_conversation_id = push_conversation

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


def _ensure_session_id(job: CronJob, registry: Any) -> None:
    """Give the job a usable transcript session id.

    A missing or non-UUID value cannot name a transcript file. After a first
    run, a missing transcript means the session was deleted, so the job must
    start a fresh conversation instead of resuming a dead id. A currently busy
    session is kept even without a transcript yet: its turn may still be
    creating the file, and swapping the id here would also bypass the busy
    guard below.
    """
    if not job.session_id or not is_canonical_uuid(job.session_id):
        job.session_id = str(uuid.uuid4())
        return
    if job.last_run_at is not None and not transcript_path(
        job.session_id, job.agent_id
    ).is_file():
        if registry.is_busy(job.session_id):
            return
        job.session_id = str(uuid.uuid4())


def _delivery_for(
    settings: Any, job: CronJob
) -> DeliveryRequest | None:
    """Resolve the job's push target against the current settings snapshot."""
    if not job.push_channel_id or not job.push_conversation_id:
        return None
    channel = next(
        (item for item in settings.channels if item.id == job.push_channel_id),
        None,
    )
    if channel is None or channel.agent_id != job.agent_id:
        logger.warning(
            "cron job %s push channel is unavailable; turn will not be delivered",
            job.id,
        )
        return None
    if channel.type != "wechat" or channel.status != "active":
        logger.warning(
            "cron job %s push channel is not an active WeChat channel; "
            "turn will not be delivered",
            job.id,
        )
        return None
    conversation = settings.channel_conversations.get(
        f"{job.push_channel_id}:{job.push_conversation_id}"
    )
    if conversation is None or conversation.channel_id != job.push_channel_id:
        logger.warning(
            "cron job %s push conversation is unavailable; turn will not be delivered",
            job.id,
        )
        return None
    return DeliveryRequest(
        channel_id=job.push_channel_id,
        external_id=job.push_conversation_id,
    )


class CronTriggerFailed(RuntimeError):
    """The schedule was consumed but the turn could not be handed to the registry."""


async def _record_trigger_failure(job_id: str) -> None:
    """Audit a consumed trigger whose turn could not be submitted."""
    try:
        async with SessionLocal() as db:
            db.add(
                AuditLog(
                    actor="system",
                    action="cron_trigger_failed",
                    entity="cron_job",
                    entity_id=job_id,
                )
            )
            await db.commit()
    except Exception:
        logger.exception(
            "cron job %s failed to record trigger failure", job_id
        )


async def prepare_trigger(
    db: AsyncSession,
    job: CronJob,
    *,
    store: Any,
    registry: Any,
    now: datetime | None = None,
    immediate: bool = False,
) -> Turn | None:
    """Apply trigger state and return the turn that should be submitted.

    The caller must commit ``db`` before submitting the returned turn. This
    keeps the schedule consumed exactly once: a crash between commit and
    submission loses one occurrence instead of replaying it, which would
    duplicate a channel push. Scheduled triggers with a busy session are
    skipped (and audited); manual triggers raise :class:`CronBusy` instead.
    """
    moment = now or utcnow()
    snapshot = await store.get()
    agent = next(
        (item for item in snapshot.agents if item.id == job.agent_id), None
    )
    if agent is None or agent.status != "active":
        if immediate:
            raise CronInactiveAgent(agent.status if agent is not None else "missing")
        _advance(job, moment)
        job.last_skipped_at = None
        return None

    _ensure_session_id(job, registry)

    if registry.is_busy(job.session_id):
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
            raise CronBusy(job.session_id, "busy")
        _advance(job, moment)
        return None

    delivery = _delivery_for(snapshot, job)
    job.last_run_at = moment
    job.last_skipped_at = None
    if not immediate:
        _advance(job, moment)
    return Turn(
        session_id=job.session_id,
        agent_id=job.agent_id,
        prompt=job.prompt,
        source="cron",
        delivery=delivery,
    )


_locks: dict[str, asyncio.Lock] = {}


def _job_lock(job_id: str) -> asyncio.Lock:
    lock = _locks.get(job_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[job_id] = lock
    return lock


async def run_due_jobs(
    now: datetime | None = None, *, store: Any, registry: Any
) -> list[str]:
    """Fire every enabled job whose next occurrence has passed.

    Each job fires at most once per call: the next occurrence is recomputed
    from ``now``, so a long outage cannot replay all missed slots. Returns the
    job ids that submitted a turn.
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
                    turn = await prepare_trigger(
                        db, job, store=store, registry=registry, now=moment
                    )
                    # Consume the schedule before handing the turn to the
                    # registry so a crash cannot replay it and duplicate a push.
                    await db.commit()
                except Exception:
                    await db.rollback()
                    logger.exception("cron job %s failed to trigger", job_id)
                    continue
                if turn is None:
                    continue
                try:
                    await registry.submit(turn)
                except Exception:
                    await _record_trigger_failure(job_id)
                    logger.warning(
                        "cron job %s failed to submit turn", job_id, exc_info=True
                    )
                    continue
                fired.append(job_id)
    return fired


async def run_job_now(
    job_id: str,
    *,
    store: Any,
    registry: Any,
    now: datetime | None = None,
    agent_id: str | None = None,
) -> CronJob:
    """Manual "run now" entry point; does not change the schedule."""
    async with _job_lock(job_id):
        async with SessionLocal() as db:
            job = await db.get(CronJob, job_id)
            if job is None or (agent_id is not None and job.agent_id != agent_id):
                raise CronNotFound("cron job not found")
            turn = await prepare_trigger(
                db,
                job,
                store=store,
                registry=registry,
                now=now,
                immediate=True,
            )
            # Consume the run before dispatch. A later submit failure is audited
            # and reported but never retried, matching the scheduler.
            await db.commit()
            await db.refresh(job)
            try:
                await registry.submit(turn)
            except Exception as exc:
                await _record_trigger_failure(job.id)
                logger.warning(
                    "cron job %s failed to submit turn", job.id, exc_info=True
                )
                raise CronTriggerFailed("failed to submit cron turn") from exc
            return job


async def detach_session(db: AsyncSession, session_id: str) -> None:
    """Compatibility stub; cron jobs no longer bind to ``sessions`` rows.

    ``session_service`` still calls this while deleting a session. Cron session
    ids are SDK transcript identities, so deleting the database session does
    not need to touch the job: the next trigger regenerates the id if the
    transcript is gone.
    """
    return None


class CronRuntime:
    def __init__(
        self,
        *,
        store: Any,
        registry: Any,
        tick_seconds: int | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
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
                await run_due_jobs(store=self.store, registry=self.registry)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cron tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
