"""Session directory backed by SDK transcripts.

Session metadata and history come straight from the Claude Agent SDK
transcripts (``list_sessions`` / ``get_session_messages``); deletion goes
through the SDK so the transcript and its subagent transcript directory are
removed together. Nothing here touches a database.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from app.services import sdk_bridge
from app.services.claude_env import workspace_for
from app.services.claude_runner import transcript_path
from app.services.identifiers import is_canonical_uuid, valid_agent_id

logger = logging.getLogger(__name__)

_TITLE_LIMIT = 80


def _text_from_message(message: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    content = message.get("content")
    if isinstance(content, str):
        return content, []
    blocks: list[dict[str, Any]] = []
    texts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                texts.append(str(block.get("text") or ""))
            elif kind in ("thinking", "tool_use"):
                blocks.append(block)
    return "\n".join(part for part in texts if part), blocks


def _session_title(summary: Any) -> str:
    title = str(summary or "") or "新会话"
    if len(title) > _TITLE_LIMIT:
        return title[:_TITLE_LIMIT] + "…"
    return title


class SessionCatalog:
    def __init__(self, store: Any, *, home: Path | None = None) -> None:
        self._store = store
        self._home = home

    def _owns(self, agent_id: str, session_id: str) -> bool:
        # Validate both ids before splicing them into a filesystem path.
        if not valid_agent_id(agent_id):
            return False
        if not is_canonical_uuid(session_id):
            return False
        return transcript_path(session_id, agent_id, home=self._home).is_file()

    async def list_sessions(self, agent_id: str) -> list[dict[str, Any]]:
        settings = await self._store.get()
        labels = {
            entry.session_id: entry
            for entry in settings.channel_conversations.values()
        }
        sessions = await asyncio.to_thread(sdk_bridge.list_sessions)
        workspace = workspace_for(agent_id, self._home).resolve()
        rows: list[dict[str, Any]] = []
        for session in sessions:
            session_cwd = str(getattr(session, "cwd", "") or "")
            if not session_cwd or Path(session_cwd).resolve() != workspace:
                continue
            session_id = str(getattr(session, "session_id", ""))
            if not session_id:
                continue
            conversation = labels.get(session_id)
            rows.append(
                {
                    "id": session_id,
                    "title": _session_title(getattr(session, "summary", "")),
                    "last_modified": int(getattr(session, "last_modified", 0) or 0),
                    "channel_name": conversation.display_name if conversation else None,
                    "channel_type": "wechat" if conversation else None,
                }
            )
        rows.sort(key=lambda item: item["last_modified"], reverse=True)
        return rows

    async def messages(
        self, session_id: str, *, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        if agent_id is not None and not self._owns(agent_id, session_id):
            return []
        rows: list[dict[str, Any]] = []
        try:
            raw_messages = await asyncio.to_thread(
                sdk_bridge.get_session_messages, session_id
            )
        except ValueError:
            # An id the SDK cannot parse is not a session; callers get an
            # empty transcript rather than a 500.
            return []
        for message in raw_messages:
            if getattr(message, "parent_tool_use_id", None):
                continue
            if str(getattr(message, "type", "")) == "user":
                content, _ = _text_from_message(getattr(message, "message", {}) or {})
                if not content.strip():
                    continue
                rows.append(
                    {
                        "id": str(getattr(message, "uuid", "")),
                        "role": "user",
                        "content": content,
                        "blocks": [],
                    }
                )
            elif str(getattr(message, "type", "")) == "assistant":
                content, blocks = _text_from_message(
                    getattr(message, "message", {}) or {}
                )
                if not content.strip() and not blocks:
                    continue
                rows.append(
                    {
                        "id": str(getattr(message, "uuid", "")),
                        "role": "assistant",
                        "content": content,
                        "blocks": blocks,
                    }
                )
        return rows

    async def rename(
        self, session_id: str, title: str, *, agent_id: str | None = None
    ) -> None:
        if not is_canonical_uuid(session_id):
            raise ValueError(f"invalid session id {session_id!r}")
        if agent_id is not None and not self._owns(agent_id, session_id):
            raise FileNotFoundError(
                f"session {session_id} not found for agent {agent_id}"
            )
        await asyncio.to_thread(sdk_bridge.rename_session, session_id, title)

    async def delete(self, agent_id: str, session_id: str) -> bool:
        if not self._owns(agent_id, session_id):
            return False
        # The SDK removes the transcript and its subagent directory. An empty
        # transcript or a concurrent delete surfaces as FileNotFoundError.
        try:
            await asyncio.to_thread(sdk_bridge.delete_session, session_id)
        except FileNotFoundError:
            return False
        await self._cleanup(agent_id, session_id)
        return True

    async def _cleanup(self, agent_id: str, session_id: str) -> None:
        """Remove per-session artifacts nothing else will reference again."""
        screenshots = workspace_for(agent_id, self._home) / "screenshots" / session_id
        await asyncio.to_thread(shutil.rmtree, screenshots, ignore_errors=True)

        def unbind(data: dict[str, Any]) -> None:
            conversations = data.get("channel_conversations")
            if not isinstance(conversations, dict):
                return
            for key, value in list(conversations.items()):
                if isinstance(value, dict) and value.get("session_id") == session_id:
                    conversations.pop(key, None)

        try:
            await self._store.mutate_raw(unbind)
        except Exception:
            # The transcript is already gone; leaving a stale binding is worse
            # than failing the request, but not worth a 500 here.
            logger.warning(
                "failed to unbind channel conversation session=%s",
                session_id,
                exc_info=True,
            )
