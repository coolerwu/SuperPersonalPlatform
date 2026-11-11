"""Per-session artifacts outside the SDK transcript.

Sessions are owned by the Claude Agent SDK, and Claude Code deletes expired
transcripts itself (the platform only writes its ``cleanupPeriodDays`` setting;
see ``claude_settings``). The platform still owns the artifacts that only exist
for one session — screenshots, channel deliveries and channel conversation
bindings — and removes them when a session is deleted manually or when a stale
channel binding is replaced.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from app.services import channel_delivery
from app.services.claude_env import workspace_for

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
