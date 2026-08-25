from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.@-]+")


@dataclass(frozen=True)
class SessionIdentity:
    session_id: str
    channel: str
    channel_account_id: str
    peer_type: str
    peer_id: str
    agent_id: str


class SessionService:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._sessions_dir = workspace / "sessions"

    def build_session_id(
        self,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
    ) -> str:
        raw_parts = [channel, channel_account_id, peer_type, peer_id, agent_id]
        safe_parts = [_safe_part(part) for part in raw_parts]
        digest = hashlib.sha1(":".join(raw_parts).encode("utf-8")).hexdigest()[:10]
        return "_".join([*safe_parts, digest])

    def get_or_create(
        self,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> SessionIdentity:
        session_id = self.build_session_id(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        now = _now()
        session_dir = self._session_dir(session_id)
        state_path = session_dir / "state.json"
        if state_path.exists():
            state = _read_json(state_path)
            created_at = str(state.get("created_at") or now)
            message_count = int(state.get("message_count") or 0)
            run_count = int(state.get("run_count") or 0)
        else:
            created_at = now
            message_count = 0
            run_count = 0

        state = {
            "session_id": session_id,
            "channel": channel,
            "channel_account_id": channel_account_id,
            "peer_type": peer_type,
            "peer_id": peer_id,
            "agent_id": agent_id,
            "status": "active",
            "created_at": created_at,
            "updated_at": now,
            "message_count": message_count,
            "run_count": run_count,
            "metadata": metadata or {},
        }
        _write_json(state_path, state)
        self._upsert_index(state)
        return SessionIdentity(
            session_id=session_id,
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        attachments: tuple[dict[str, Any], ...] = (),
        run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        text = content.strip()
        if not text and not attachments:
            return

        session_dir = self._session_dir(session_id)
        state = self._read_state(session_id)
        now = _now()
        message_count = int(state.get("message_count") or 0) + 1
        message = {
            "seq": message_count,
            "session_id": session_id,
            "role": role,
            "content": text,
            "run_id": run_id,
            "created_at": now,
            "metadata": metadata or {},
        }
        if attachments:
            message["attachments"] = list(attachments)
        _append_jsonl(session_dir / "messages.jsonl", message)
        state["message_count"] = message_count
        state["last_message_at"] = now
        state["updated_at"] = now
        _write_json(session_dir / "state.json", state)
        self._upsert_index(state)

    def save_attachments(
        self,
        session_id: str,
        attachments: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        if not attachments:
            return ()
        state = self._read_state(session_id)
        message_seq = int(state.get("message_count") or 0) + 1
        saved: list[dict[str, Any]] = []
        for index, attachment in enumerate(attachments, start=1):
            payload = _attachment_bytes(attachment)
            if not payload:
                continue
            raw_kind = str(attachment.get("type") or "file").strip().lower()
            kind = raw_kind if raw_kind in {"image", "file"} else "file"
            mime = str(attachment.get("mime") or _guess_mime(attachment) or "application/octet-stream").strip()
            attachment_id = _safe_part(str(attachment.get("id") or f"{kind}_{index}"))[:48]
            extension = _attachment_extension(attachment, mime)
            filename = _safe_filename(str(attachment.get("filename") or f"{attachment_id}{extension}"))
            relative_path = Path("attachments") / str(message_seq) / filename
            target = self._session_dir(session_id) / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            saved.append(
                {
                    "id": attachment_id,
                    "type": kind,
                    "mime": mime,
                    "filename": filename,
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "session_path": relative_path.as_posix(),
                    "workspace_path": f"sessions/{session_id}/{relative_path.as_posix()}",
                }
            )
        return tuple(saved)

    def append_run(
        self,
        session_id: str,
        *,
        run_id: str,
        status: str,
        source: str,
        agent_id: str,
    ) -> None:
        state = self._read_state(session_id)
        now = _now()
        run_count = int(state.get("run_count") or 0) + 1
        _append_jsonl(
            self._session_dir(session_id) / "runs.jsonl",
            {
                "seq": run_count,
                "session_id": session_id,
                "run_id": run_id,
                "status": status,
                "source": source,
                "agent_id": agent_id,
                "created_at": now,
            },
        )
        state["run_count"] = run_count
        state["last_run_id"] = run_id
        state["updated_at"] = now
        _write_json(self._session_dir(session_id) / "state.json", state)
        self._upsert_index(state)

    def read_messages(self, session_id: str, *, limit: int = 40) -> list[dict[str, Any]]:
        messages_path = self._session_dir(session_id) / "messages.jsonl"
        if not messages_path.exists():
            return []
        messages: list[dict[str, Any]] = []
        for line in messages_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                messages.append(item)
        return messages[-limit:]

    def exists(self, session_id: str) -> bool:
        return (self._session_dir(session_id) / "state.json").exists()

    def _read_state(self, session_id: str) -> dict[str, Any]:
        state_path = self._session_dir(session_id) / "state.json"
        if not state_path.exists():
            raise FileNotFoundError(session_id)
        return _read_json(state_path)

    def _upsert_index(self, state: dict[str, Any]) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        index = self._read_index()
        sessions = index.get("sessions") if isinstance(index, dict) else []
        if not isinstance(sessions, list):
            sessions = []
        summary = {
            "session_id": state.get("session_id", ""),
            "channel": state.get("channel", ""),
            "channel_account_id": state.get("channel_account_id", ""),
            "peer_type": state.get("peer_type", ""),
            "peer_id": state.get("peer_id", ""),
            "agent_id": state.get("agent_id", ""),
            "status": state.get("status", "active"),
            "last_run_id": state.get("last_run_id", ""),
            "updated_at": state.get("updated_at", ""),
            "message_count": state.get("message_count", 0),
            "run_count": state.get("run_count", 0),
        }
        next_sessions = [
            item for item in sessions if isinstance(item, dict) and item.get("session_id") != summary["session_id"]
        ]
        next_sessions.insert(0, summary)
        _write_json(self._index_path, {"schema_version": 1, "sessions": next_sessions})

    def _read_index(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {"schema_version": 1, "sessions": []}
        return _read_json(self._index_path)

    def _session_dir(self, session_id: str) -> Path:
        return self._sessions_dir / session_id

    @property
    def _index_path(self) -> Path:
        return self._sessions_dir / "index.json"


def _safe_part(value: str) -> str:
    safe = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return safe[:80] or "unknown"


def _safe_filename(value: str) -> str:
    safe = _SAFE_ID_RE.sub("_", Path(value or "").name).strip("._-")
    return safe[:120] or "attachment"


def _attachment_bytes(attachment: dict[str, Any]) -> bytes:
    raw_bytes = attachment.get("bytes")
    if isinstance(raw_bytes, bytes):
        return raw_bytes
    if isinstance(raw_bytes, bytearray):
        return bytes(raw_bytes)
    data_url = str(attachment.get("data_url") or "")
    if data_url.startswith("data:") and "," in data_url:
        import base64

        try:
            return base64.b64decode(data_url.split(",", 1)[1], validate=False)
        except Exception:
            return b""
    content_base64 = str(
        attachment.get("content_base64")
        or attachment.get("base64")
        or attachment.get("content")
        or ""
    )
    if content_base64:
        import base64

        try:
            return base64.b64decode(content_base64, validate=False)
        except Exception:
            return b""
    source_path = attachment.get("source_path")
    if isinstance(source_path, Path) and source_path.is_file():
        return source_path.read_bytes()
    return b""


def _guess_mime(attachment: dict[str, Any]) -> str:
    data_url = str(attachment.get("data_url") or "")
    if data_url.startswith("data:") and ";" in data_url:
        return data_url.removeprefix("data:").split(";", 1)[0]
    if str(attachment.get("type") or "").lower() == "image":
        return "image/jpeg"
    return ""


def _attachment_extension(attachment: dict[str, Any], mime: str) -> str:
    filename = str(attachment.get("filename") or "")
    suffix = Path(filename).suffix
    if suffix:
        return suffix
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }.get(mime.lower(), ".bin")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
