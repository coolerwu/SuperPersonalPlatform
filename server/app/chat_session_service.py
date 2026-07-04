import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from server.domain.sessions import (
    ChatCheckpointData,
    ChatImageData,
    ChatMessageData,
    ChatSession,
    ChatSessionNotFoundError,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ChatSessionSummary:
    id: str
    title: str
    agent_id: str
    message_count: int
    created_at: str
    updated_at: str


class ChatSessionService:
    def __init__(self, workspace: Path) -> None:
        self._dir = workspace / "sessions"

    def _sessions_dir(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def _session_path(self, session_id: str) -> Path:
        return self._sessions_dir() / f"{session_id}.json"

    def list_sessions(self, agent_id: str | None = None) -> list[ChatSessionSummary]:
        summaries: list[ChatSessionSummary] = []
        for path in sorted(self._sessions_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if agent_id is not None and str(raw.get("agent_id") or "") != agent_id:
                continue
            summaries.append(
                ChatSessionSummary(
                    id=raw.get("id", path.stem),
                    title=raw.get("title", ""),
                    agent_id=raw.get("agent_id", ""),
                    message_count=len(raw.get("messages", [])),
                    created_at=raw.get("created_at", ""),
                    updated_at=raw.get("updated_at", ""),
                )
            )
        return summaries

    def get_session(self, session_id: str, agent_id: str | None = None) -> ChatSession:
        path = self._session_path(session_id)
        if not path.exists():
            raise ChatSessionNotFoundError(f"session {session_id} not found")
        session = self._parse_session(json.loads(path.read_text(encoding="utf-8")))
        if agent_id is not None and session.agent_id != agent_id:
            raise ChatSessionNotFoundError(f"session {session_id} does not belong to agent {agent_id}")
        return session

    def create_session(self, agent_id: str, title: str = "") -> ChatSession:
        session_id = f"session-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        session = ChatSession(
            id=session_id,
            title=title.strip(),
            agent_id=agent_id.strip(),
            messages=(),
            created_at=now,
            updated_at=now,
        )
        self._write_session(session)
        return session

    def delete_session(self, session_id: str) -> None:
        path = self._session_path(session_id)
        if not path.exists():
            raise ChatSessionNotFoundError(f"session {session_id} not found")
        path.unlink()

    def append_message(self, session_id: str, message: ChatMessageData) -> ChatSession:
        path = self._session_path(session_id)
        if not path.exists():
            raise ChatSessionNotFoundError(f"session {session_id} not found")
        raw = json.loads(path.read_text(encoding="utf-8"))
        session = self._parse_session(raw)
        new_title = session.title
        if not new_title and message.role == "user" and message.content.strip():
            new_title = message.content.strip()[:30]
        now = _now_iso()
        updated_message = ChatMessageData(
            role=message.role,
            content=message.content,
            images=message.images,
            checkpoints=message.checkpoints,
            run_id=message.run_id,
            created_at=now,
        )
        updated = ChatSession(
            id=session.id,
            title=new_title,
            agent_id=session.agent_id,
            messages=(*session.messages, updated_message),
            created_at=session.created_at,
            updated_at=now,
        )
        self._write_session(updated)
        return updated

    def update_title(self, session_id: str, title: str) -> ChatSession:
        path = self._session_path(session_id)
        if not path.exists():
            raise ChatSessionNotFoundError(f"session {session_id} not found")
        raw = json.loads(path.read_text(encoding="utf-8"))
        session = self._parse_session(raw)
        updated = ChatSession(
            id=session.id,
            title=title.strip(),
            agent_id=session.agent_id,
            messages=session.messages,
            created_at=session.created_at,
            updated_at=_now_iso(),
        )
        self._write_session(updated)
        return updated

    def _parse_session(self, raw: dict) -> ChatSession:
        messages: list[ChatMessageData] = []
        for msg in raw.get("messages", []):
            messages.append(
                ChatMessageData(
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    images=tuple(
                        ChatImageData(mime_type=img["mime_type"], data=img["data"])
                        for img in msg.get("images", [])
                    ),
                    checkpoints=tuple(
                        ChatCheckpointData(
                            stage=cp.get("stage", ""),
                            title=cp.get("title", ""),
                            detail=cp.get("detail", ""),
                        )
                        for cp in msg.get("checkpoints", [])
                    ),
                    run_id=msg.get("run_id", ""),
                    created_at=msg.get("created_at", ""),
                )
            )
        return ChatSession(
            id=raw.get("id", ""),
            title=raw.get("title", ""),
            agent_id=raw.get("agent_id", ""),
            messages=tuple(messages),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
        )

    def _session_to_dict(self, session: ChatSession) -> dict:
        return {
            "id": session.id,
            "title": session.title,
            "agent_id": session.agent_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "images": [
                        {"mime_type": img.mime_type, "data": img.data} for img in msg.images
                    ],
                    "checkpoints": [
                        {"stage": cp.stage, "title": cp.title, "detail": cp.detail}
                        for cp in msg.checkpoints
                    ],
                    "run_id": msg.run_id,
                    "created_at": msg.created_at,
                }
                for msg in session.messages
            ],
        }

    def _write_session(self, session: ChatSession) -> None:
        path = self._session_path(session.id)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(self._session_to_dict(session), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
