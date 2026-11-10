"""Retention cleanup for SDK transcripts.

Sessions are owned by the Claude Agent SDK: each one is a transcript file in
``<home>/claude/projects/<agent_id>/<session_id>.jsonl``. Cleanup therefore
walks those transcripts instead of querying a database, and removes the
per-session artifacts that only existed for that session (screenshots, channel
deliveries and channel conversation bindings). A session with a turn lease or a
busy in-process registry entry is never touched.

Artifacts are removed before the transcript so an interrupted sweep (cancel,
crash, failed SDK delete) leaves the transcript behind and can be retried: every
artifact step is idempotent and independently guarded.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from app.config import settings as app_settings
from app.models import utcnow
from app.services import channel_delivery, sdk_bridge, turn_lease
from app.services.claude_env import workspace_for
from app.services.claude_runner import transcript_dir
from app.services.identifiers import is_canonical_uuid, valid_agent_id

logger = logging.getLogger(__name__)


async def cleanup_session_artifacts(
    agent_id: str, session_id: str, *, home: Path | None, store: Any
) -> None:
    """Remove per-session artifacts outside the SDK transcript.

    Every step is independent: one failure is logged and the rest still run, so
    a half-cleaned session can be retried without losing the transcript.
    """
    screenshots = workspace_for(agent_id, home) / "screenshots" / session_id
    try:
        if screenshots.is_dir():
            await asyncio.to_thread(shutil.rmtree, screenshots, True)
    except Exception:
        logger.warning(
            "failed to remove screenshots session=%s", session_id, exc_info=True
        )

    try:
        await channel_delivery.delete_for_session(session_id)
    except Exception:
        logger.warning(
            "failed to delete channel deliveries session=%s",
            session_id,
            exc_info=True,
        )

    if store is not None:

        def unbind(data: dict[str, Any]) -> None:
            conversations = data.get("channel_conversations")
            if not isinstance(conversations, dict):
                return
            for key, value in list(conversations.items()):
                if isinstance(value, dict) and value.get("session_id") == session_id:
                    conversations.pop(key, None)

        try:
            await store.mutate_raw(unbind)
        except Exception:
            logger.warning(
                "failed to unbind channel conversation session=%s",
                session_id,
                exc_info=True,
            )


async def purge_expired_transcripts(
    *,
    agent_id: str,
    retention_days: int,
    home: Path | None = None,
    store: Any = None,
    registry: Any = None,
) -> int:
    """Delete transcripts idle beyond the retention window for one agent.

    ``session_id`` comes from the transcript filename, so nothing else has to
    enumerate sessions. Sessions with a live turn lease, a busy registry entry,
    a non-UUID filename, or a fresh mtime are skipped. Artifacts are removed
    before the transcript; failures for one session are logged and do not abort
    the rest of the sweep.
    """
    if not valid_agent_id(agent_id):
        logger.warning("invalid agent id for transcript cleanup: %r", agent_id)
        return 0
    if retention_days <= 0:
        return 0
    cutoff = utcnow().timestamp() - retention_days * 86400
    running = set(await turn_lease.list_all())
    directory = transcript_dir(agent_id, home=home)
    removed = 0
    if not directory.is_dir():
        return 0
    skipped_invalid = 0
    for path in sorted(directory.glob("*.jsonl")):
        session_id = path.stem
        if not is_canonical_uuid(session_id):
            # Empty or foreign files are not SDK sessions; never hand them to
            # the SDK and never unlink them here.
            skipped_invalid += 1
            continue
        if session_id in running:
            continue
        if registry is not None and registry.is_busy(session_id):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        # Artifacts first: a cancelled or failed sweep leaves the transcript so
        # the next run repeats the idempotent cleanup.
        await cleanup_session_artifacts(
            agent_id, session_id, home=home, store=store
        )
        try:
            # The SDK removes the transcript and its subagent directory.
            await asyncio.to_thread(sdk_bridge.delete_session, session_id)
        except FileNotFoundError:
            # The SDK only accepts non-empty transcripts, so an empty file (or
            # one removed concurrently) reports not-found; finish the delete
            # ourselves, including the subagent directory.
            try:
                path.unlink(missing_ok=True)
                await asyncio.to_thread(shutil.rmtree, directory / session_id, True)
            except OSError:
                logger.warning(
                    "failed to remove orphan transcript session=%s",
                    session_id,
                    exc_info=True,
                )
                continue
        except Exception:
            logger.warning(
                "failed to delete transcript session=%s", session_id, exc_info=True
            )
            continue
        if registry is not None:
            try:
                await registry.close_session(session_id)
            except Exception:
                logger.warning(
                    "failed to release cached client session=%s",
                    session_id,
                    exc_info=True,
                )
        removed += 1
    if skipped_invalid:
        logger.warning(
            "skipped %d non-UUID transcript file(s) under %s",
            skipped_invalid,
            directory,
        )
    return removed


class SessionCleanupRuntime:
    """Periodically purge transcripts past the configured retention window."""

    def __init__(
        self,
        *,
        store: Any,
        registry: Any = None,
        interval_seconds: int | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.interval = max(
            60,
            interval_seconds
            if interval_seconds is not None
            else app_settings.session_cleanup_interval_seconds,
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="session-cleanup")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("session cleanup failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> int:
        settings = await self.store.get()
        days = settings.runtime.session_retention_days
        removed = 0
        for agent in settings.agents:
            if not valid_agent_id(agent.id):
                logger.warning(
                    "skipping transcript cleanup for invalid agent id %r", agent.id
                )
                continue
            removed += await purge_expired_transcripts(
                agent_id=agent.id,
                retention_days=days,
                store=self.store,
                registry=self.registry,
            )
        return removed
