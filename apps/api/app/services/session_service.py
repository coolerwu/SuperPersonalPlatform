"""Retention cleanup for SDK transcripts.

Sessions are owned by the Claude Agent SDK: each one is a transcript file in
``<home>/claude/projects/<agent_id>/<session_id>.jsonl``. Cleanup therefore
walks those transcripts instead of querying a database, and removes the
per-session artifacts that only existed for that session (screenshots, channel
deliveries and channel conversation bindings). A session with a turn lease is
never touched.
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

logger = logging.getLogger(__name__)


async def _cleanup_session_artifacts(
    agent_id: str, session_id: str, *, home: Path | None, store: Any
) -> None:
    screenshots = workspace_for(agent_id, home) / "screenshots" / session_id
    if screenshots.is_dir():
        await asyncio.to_thread(shutil.rmtree, screenshots, True)
    await channel_delivery.delete_for_session(session_id)
    if store is not None:

        def unbind(data: dict[str, Any]) -> None:
            conversations = data.get("channel_conversations") or {}
            for key in list(conversations):
                if conversations[key].get("session_id") == session_id:
                    del conversations[key]

        await store.mutate_raw(unbind)


async def purge_expired_transcripts(
    *,
    agent_id: str,
    retention_days: int,
    home: Path | None = None,
    store: Any = None,
) -> int:
    """Delete transcripts idle beyond the retention window for one agent.

    ``session_id`` comes from the transcript filename, so nothing else has to
    enumerate sessions. A session with a live turn lease is skipped; failures
    for one session are logged and do not abort the rest of the sweep.
    """
    if retention_days <= 0:
        return 0
    cutoff = utcnow().timestamp() - retention_days * 86400
    running = set(await turn_lease.list_all())
    directory = transcript_dir(agent_id, home=home)
    removed = 0
    if not directory.is_dir():
        return 0
    for path in sorted(directory.glob("*.jsonl")):
        session_id = path.stem
        if session_id in running:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        try:
            # The SDK removes the transcript and its subagent directory.
            await asyncio.to_thread(sdk_bridge.delete_session, session_id)
        except FileNotFoundError:
            path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "failed to delete transcript session=%s", session_id, exc_info=True
            )
            continue
        try:
            await _cleanup_session_artifacts(
                agent_id, session_id, home=home, store=store
            )
        except Exception:
            logger.warning(
                "cleanup artifacts failed session=%s", session_id, exc_info=True
            )
        removed += 1
    return removed


class SessionCleanupRuntime:
    """Periodically purge transcripts past the configured retention window."""

    def __init__(self, *, store: Any, interval_seconds: int | None = None) -> None:
        self.store = store
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
            removed += await purge_expired_transcripts(
                agent_id=agent.id, retention_days=days, store=self.store
            )
        return removed
