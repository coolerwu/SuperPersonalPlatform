"""Session directory backed by SDK transcripts.

Session metadata and history come straight from the Claude Agent SDK
transcripts (``list_sessions`` / ``get_session_messages``); deletion removes
the transcript file. Nothing here touches a database.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.services import sdk_bridge
from app.services.claude_env import workspace_for
from app.services.claude_runner import transcript_path


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


class SessionCatalog:
    def __init__(self, store: Any, *, home: Path | None = None) -> None:
        self._store = store
        self._home = home

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
                    "title": str(getattr(session, "summary", "") or "新会话"),
                    "last_modified": int(getattr(session, "last_modified", 0) or 0),
                    "channel_name": conversation.display_name if conversation else None,
                    "channel_type": "wechat" if conversation else None,
                }
            )
        rows.sort(key=lambda item: item["last_modified"], reverse=True)
        return rows

    async def messages(self, session_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        raw_messages = await asyncio.to_thread(
            sdk_bridge.get_session_messages, session_id
        )
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

    async def rename(self, session_id: str, title: str) -> None:
        await asyncio.to_thread(sdk_bridge.rename_session, session_id, title)

    async def delete(self, agent_id: str, session_id: str) -> bool:
        # The id is spliced into a file name, so never let a caller-supplied
        # value escape the agent's project directory.
        if (
            not session_id
            or "/" in session_id
            or "\\" in session_id
            or session_id in {".", ".."}
        ):
            return False
        path = transcript_path(session_id, agent_id, home=self._home)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
