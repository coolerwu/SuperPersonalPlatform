from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import (
    ChannelDelivery,
    ChannelRunLink,
    Message,
    Run,
    RunEvent,
    RunMemory,
    Session,
    utcnow,
)
from app.services import app_config, channel_service, cron_service
from app.services.workspace import WorkspaceError, resolve_workspace_path

logger = logging.getLogger(__name__)

SESSION_DELETED = "deleted"
SESSION_ACTIVE = "active"
SESSION_MISSING = "missing"
CLEANUP_BATCH_SIZE = 200
CLEANUP_MAX_BATCHES = 10


async def delete_session_cascade(db: AsyncSession, session_id: str) -> str:
    """Delete one session and everything bound to it.

    Shared by the HTTP endpoint and the retention task so both keep the same
    semantics: an active Run blocks deletion, channel conversations are
    unlinked, cron jobs lose their session, and the checkpoint/screenshot
    files are removed.
    """
    session = await db.get(Session, session_id)
    if session is None:
        return SESSION_MISSING
    agent_id = session.agent_id
    active = await db.scalar(
        select(Run)
        .where(Run.session_id == session_id, Run.status.in_(("queued", "running")))
        .limit(1)
    )
    if active is not None:
        return SESSION_ACTIVE
    await channel_service.unlink_session(db, session_id)
    await cron_service.detach_session(db, session_id)
    run_ids = list((await db.scalars(select(Run.id).where(Run.session_id == session_id))).all())
    if run_ids:
        await db.execute(delete(ChannelDelivery).where(ChannelDelivery.run_id.in_(run_ids)))
        await db.execute(delete(ChannelRunLink).where(ChannelRunLink.run_id.in_(run_ids)))
        await db.execute(delete(RunMemory).where(RunMemory.run_id.in_(run_ids)))
        await db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
        await db.execute(delete(Run).where(Run.id.in_(run_ids)))
    await db.execute(delete(Message).where(Message.session_id == session_id))
    await db.commit()
    await db.delete(session)
    await db.commit()
    checkpoint: Path = settings.checkpoint_dir / f"{session_id}.sqlite"
    if checkpoint.exists():
        checkpoint.unlink(missing_ok=True)
    try:
        workspace = resolve_workspace_path(agent_id, create=False)
    except WorkspaceError:
        workspace = None
    if workspace is not None:
        screenshots = workspace / "screenshots" / session_id
        if screenshots.exists():
            shutil.rmtree(screenshots, ignore_errors=True)
    return SESSION_DELETED


async def cleanup_inactive_sessions(
    retention_days: int | None = None,
    *,
    now: datetime | None = None,
    batch_size: int = CLEANUP_BATCH_SIZE,
) -> int:
    """Delete one batch of sessions idle beyond the retention window.

    When ``retention_days`` is omitted, the value stored in the config page
    (``app_configs``) is used; the env setting only seeds that row on first
    creation.
    """
    last_message_at = (
        select(func.max(Message.created_at))
        .where(Message.session_id == Session.id)
        .correlate(Session)
        .scalar_subquery()
    )
    last_activity = func.coalesce(last_message_at, Session.created_at)
    active_run = (
        select(Run.id)
        .where(
            Run.session_id == Session.id,
            Run.status.in_(("queued", "running")),
        )
        .correlate(Session)
        .exists()
    )
    deleted = 0
    async with SessionLocal() as db:
        if retention_days is None:
            row = await app_config.get_or_create(db)
            days = int(row.session_retention_days)
        else:
            days = retention_days
        if days <= 0:
            return 0
        cutoff = (now or utcnow()) - timedelta(days=days)
        session_ids = list(
            (
                await db.scalars(
                    select(Session.id)
                    .where(last_activity < cutoff, ~active_run)
                    .order_by(last_activity)
                    .limit(batch_size)
                )
            ).all()
        )
        for session_id in session_ids:
            try:
                result = await delete_session_cascade(db, session_id)
            except Exception:
                await db.rollback()
                logger.exception("failed to delete inactive session %s", session_id)
                continue
            if result == SESSION_DELETED:
                deleted += 1
    return deleted


class SessionCleanupRuntime:
    """Periodically delete sessions whose last activity is too old."""

    def __init__(
        self,
        *,
        retention_days: int | None = None,
        interval_seconds: int | None = None,
    ) -> None:
        # None means "follow the value saved on the config page"; an explicit
        # int is used by tests and immediate callers.
        self.retention_days = retention_days
        self.interval_seconds = max(
            60,
            interval_seconds
            if interval_seconds is not None
            else settings.session_cleanup_interval_seconds,
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.retention_days is not None and self.retention_days <= 0:
            logger.info("session cleanup disabled (retention days = 0)")
            return
        self._task = asyncio.create_task(self._loop(), name="session-cleanup")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                days = self.retention_days
                if days is None:
                    days = await app_config.get_session_retention_days()
                if days > 0:
                    total = 0
                    for _ in range(CLEANUP_MAX_BATCHES):
                        removed = await cleanup_inactive_sessions(days)
                        total += removed
                        if removed < CLEANUP_BATCH_SIZE:
                            break
                    if total:
                        logger.info(
                            "deleted %d sessions inactive for %d+ days",
                            total,
                            days,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("session cleanup failed")
            await asyncio.sleep(self.interval_seconds)
