from __future__ import annotations

import hashlib
import json
import re
import uuid
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
        active_key = self.build_session_id(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        binding = self._find_active_binding(active_key)
        if binding:
            session_id = str(binding.get("session_id") or "").strip()
            if session_id and self.exists(session_id):
                current = self._read_state(session_id)
                if str(current.get("agent_id") or "").strip() == agent_id:
                    if self._summary_matches_identity(
                        current,
                        active_key=active_key,
                        channel=channel,
                        channel_account_id=channel_account_id,
                        peer_type=peer_type,
                        peer_id=peer_id,
                        agent_id=agent_id,
                    ):
                        self._touch_session(
                            session_id,
                            channel=channel,
                            channel_account_id=channel_account_id,
                            peer_type=peer_type,
                            peer_id=peer_id,
                            agent_id=agent_id,
                            active_key=active_key,
                            generation=int(binding.get("generation") or 1),
                            metadata=metadata,
                        )
                    else:
                        self._activate_session(session_id)
                    self._upsert_active_binding(
                        active_key=active_key,
                        session_id=session_id,
                        channel=channel,
                        channel_account_id=channel_account_id,
                        peer_type=peer_type,
                        peer_id=peer_id,
                        agent_id=agent_id,
                        generation=int(binding.get("generation") or 1),
                    )
                    return SessionIdentity(
                        session_id=session_id,
                        channel=channel,
                        channel_account_id=channel_account_id,
                        peer_type=peer_type,
                        peer_id=peer_id,
                        agent_id=agent_id,
                    )

        legacy_session_id = active_key
        if self.exists(legacy_session_id):
            state = self._touch_session(
                legacy_session_id,
                channel=channel,
                channel_account_id=channel_account_id,
                peer_type=peer_type,
                peer_id=peer_id,
                agent_id=agent_id,
                active_key=active_key,
                generation=int(binding.get("generation") or 1) if binding else 1,
                metadata=metadata,
            )
            self._upsert_active_binding(
                active_key=active_key,
                session_id=legacy_session_id,
                channel=channel,
                channel_account_id=channel_account_id,
                peer_type=peer_type,
                peer_id=peer_id,
                agent_id=agent_id,
                generation=int(state.get("generation") or 1),
            )
            return SessionIdentity(
                session_id=legacy_session_id,
                channel=channel,
                channel_account_id=channel_account_id,
                peer_type=peer_type,
                peer_id=peer_id,
                agent_id=agent_id,
            )

        generation = int(binding.get("generation") or 0) + 1 if binding else 1
        session_id = self._new_session_id(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        return self._create_session(
            session_id=session_id,
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
            active_key=active_key,
            generation=generation,
            metadata=metadata,
        )

    def clear_active(
        self,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SessionIdentity:
        active_key = self.build_session_id(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        binding = self._find_active_binding(active_key)
        generation = int(binding.get("generation") or 0) if binding else 0
        old_session_id = str(binding.get("session_id") or "").strip() if binding else ""
        if not old_session_id and self.exists(active_key):
            old_session_id = active_key
            generation = 1
        session = self._create_session(
            session_id=self._new_session_id(
                channel=channel,
                channel_account_id=channel_account_id,
                peer_type=peer_type,
                peer_id=peer_id,
                agent_id=agent_id,
            ),
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
            active_key=active_key,
            generation=generation + 1,
            metadata=metadata,
        )
        self._archive_if_unbound(old_session_id, reason=reason)
        return session

    def active_summary(
        self,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
    ) -> dict[str, Any]:
        active_key = self.build_session_id(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        binding = self._find_active_binding(active_key)
        session_id = str(binding.get("session_id") or "").strip() if binding else ""
        if session_id and self.exists(session_id):
            return self.session_summary(session_id)
        legacy_session_id = active_key
        if self.exists(legacy_session_id):
            return self.session_summary(legacy_session_id)
        return {}

    def related_summaries_for_identity(
        self,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        active_key = self.build_session_id(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        active_session_ids = self._active_session_ids()
        related: list[dict[str, Any]] = []
        for item in self._read_index().get("sessions", []):
            if not isinstance(item, dict):
                continue
            if not self._summary_matches_identity(
                item,
                active_key=active_key,
                channel=channel,
                channel_account_id=channel_account_id,
                peer_type=peer_type,
                peer_id=peer_id,
                agent_id=agent_id,
            ):
                continue
            session_id = str(item.get("session_id") or "").strip()
            if not session_id or not self.exists(session_id):
                continue
            summary = dict(item)
            summary["active"] = session_id in active_session_ids
            related.append(summary)
        related.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        max_items = min(max(int(limit or 10), 1), 30)
        return related[:max_items]

    def summaries_for_agent(
        self,
        *,
        agent_id: str,
        selected_active_key: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_agent_id = str(agent_id or "").strip()
        selected_binding = self._find_active_binding(selected_active_key) if selected_active_key else None
        selected_session_id = str(selected_binding.get("session_id") or "").strip() if selected_binding else ""
        active_session_ids = self._active_session_ids()
        sessions: list[dict[str, Any]] = []
        for item in self._read_index().get("sessions", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("agent_id") or "").strip() != normalized_agent_id:
                continue
            session_id = str(item.get("session_id") or "").strip()
            if not session_id or not self.exists(session_id):
                continue
            summary = dict(item)
            summary["active"] = session_id in active_session_ids
            summary["selected"] = session_id == selected_session_id
            sessions.append(summary)
        sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        max_items = min(max(int(limit or 100), 1), 200)
        return sessions[:max_items]

    def select_active_for_agent(
        self,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
        selector: str,
    ) -> dict[str, Any]:
        active_key = self.build_session_id(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        target = _select_session_summary(
            self.summaries_for_agent(agent_id=agent_id, selected_active_key=active_key, limit=200),
            selector,
        )
        if target is None:
            raise ValueError("session not found for current agent")

        target_id = str(target.get("session_id") or "").strip()
        current_binding = self._find_active_binding(active_key)
        current_id = str(current_binding.get("session_id") or "").strip() if current_binding else ""
        generation = int(current_binding.get("generation") or 1) if current_binding else 1
        self._activate_session(target_id)
        self._upsert_active_binding(
            active_key=active_key,
            session_id=target_id,
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
            generation=generation,
        )
        if current_id and current_id != target_id:
            self._archive_if_unbound(current_id)
        summary = self.session_summary(target_id)
        summary["active"] = True
        summary["selected"] = True
        return summary

    def switch_active(
        self,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
        selector: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        related = self.related_summaries_for_identity(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
            limit=30,
        )
        target = _select_session_summary(related, selector)
        if target is None:
            raise ValueError("session not found for current identity")
        active_key = self.build_session_id(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        target_id = str(target.get("session_id") or "").strip()
        current = self.active_summary(
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )
        current_id = str(current.get("session_id") or "").strip()
        target_state = self._touch_session(
            target_id,
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
            active_key=active_key,
            generation=int(target.get("generation") or 1),
            metadata=metadata,
        )
        self._upsert_active_binding(
            active_key=active_key,
            session_id=target_id,
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
            generation=int(target_state.get("generation") or target.get("generation") or 1),
        )
        if current_id and current_id != target_id:
            self._archive_if_unbound(current_id)
        summary = dict(target_state)
        summary["active"] = True
        return summary

    def _create_session(
        self,
        *,
        session_id: str,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
        active_key: str,
        generation: int,
        metadata: dict[str, Any] | None,
    ) -> SessionIdentity:
        now = _now()
        session_dir = self._session_dir(session_id)
        state_path = session_dir / "state.json"
        state = {
            "session_id": session_id,
            "active_key": active_key,
            "channel": channel,
            "channel_account_id": channel_account_id,
            "peer_type": peer_type,
            "peer_id": peer_id,
            "agent_id": agent_id,
            "generation": generation,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
            "run_count": 0,
            "metadata": metadata or {},
        }
        _write_json(state_path, state)
        self._upsert_index(state)
        self._upsert_active_binding(
            active_key=active_key,
            session_id=session_id,
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
            generation=generation,
        )
        return SessionIdentity(
            session_id=session_id,
            channel=channel,
            channel_account_id=channel_account_id,
            peer_type=peer_type,
            peer_id=peer_id,
            agent_id=agent_id,
        )

    def _touch_session(
        self,
        session_id: str,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
        active_key: str,
        generation: int,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = _now()
        session_dir = self._session_dir(session_id)
        state_path = session_dir / "state.json"
        state = _read_json(state_path) if state_path.exists() else {}
        current_metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        if metadata:
            current_metadata = {**current_metadata, **metadata}
        state.update(
            {
                "session_id": session_id,
                "active_key": active_key,
                "channel": channel,
                "channel_account_id": channel_account_id,
                "peer_type": peer_type,
                "peer_id": peer_id,
                "agent_id": agent_id,
                "generation": generation,
                "status": "active",
                "created_at": str(state.get("created_at") or now),
                "updated_at": now,
                "message_count": int(state.get("message_count") or 0),
                "run_count": int(state.get("run_count") or 0),
                "metadata": current_metadata,
            }
        )
        _write_json(state_path, state)
        self._upsert_index(state)
        return state

    def _activate_session(self, session_id: str) -> dict[str, Any]:
        state = self._read_state(session_id)
        if str(state.get("status") or "") != "active":
            state["status"] = "active"
            state["updated_at"] = _now()
            _write_json(self._session_dir(session_id) / "state.json", state)
            self._upsert_index(state)
        return state

    def _archive_if_unbound(self, session_id: str, *, reason: str = "") -> None:
        if not session_id or not self.exists(session_id) or session_id in self._active_session_ids():
            return
        state = self._read_state(session_id)
        now = _now()
        state["status"] = "archived"
        state["updated_at"] = now
        if reason:
            state["cleared_at"] = now
            state["clear_reason"] = reason
        _write_json(self._session_dir(session_id) / "state.json", state)
        self._upsert_index(state)

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

    def search_messages(
        self,
        session_id: str,
        *,
        query: str,
        limit: int = 8,
        role: str = "",
    ) -> list[dict[str, Any]]:
        messages = self.read_messages(session_id, limit=5000)
        normalized_role = str(role or "").strip().lower()
        max_items = min(max(int(limit or 8), 1), 20)
        scored: list[tuple[int, dict[str, Any]]] = []
        for message in messages:
            if normalized_role and str(message.get("role") or "").lower() != normalized_role:
                continue
            score = _message_search_score(message, query)
            if score <= 0:
                continue
            scored.append((score, message))
        scored.sort(key=lambda item: (item[0], int(item[1].get("seq") or 0)), reverse=True)
        return [message for _, message in scored[:max_items]]

    def search_related_sessions(
        self,
        session_id: str,
        *,
        query: str,
        limit: int = 8,
        role: str = "",
    ) -> list[dict[str, Any]]:
        current = self.session_summary(session_id)
        if not current:
            return []
        normalized_role = str(role or "").strip().lower()
        max_items = min(max(int(limit or 8), 1), 20)
        active_session_ids = self._active_session_ids()
        groups: list[dict[str, Any]] = []
        for summary in self._related_session_summaries(current):
            candidate_id = str(summary.get("session_id") or "").strip()
            if not candidate_id or not self.exists(candidate_id):
                continue
            hits: list[dict[str, Any]] = []
            score = 0
            for message in self.read_messages(candidate_id, limit=5000):
                if normalized_role and str(message.get("role") or "").lower() != normalized_role:
                    continue
                message_score = _message_search_score(message, query)
                if message_score <= 0:
                    continue
                score += message_score
                enriched = dict(message)
                enriched["_score"] = message_score
                hits.append(enriched)
            if not hits:
                continue
            hits.sort(key=lambda item: (int(item.get("_score") or 0), int(item.get("seq") or 0)), reverse=True)
            groups.append(
                {
                    "session": {
                        "session_id": candidate_id,
                        "channel": summary.get("channel", ""),
                        "channel_account_id": summary.get("channel_account_id", ""),
                        "peer_type": summary.get("peer_type", ""),
                        "peer_id": summary.get("peer_id", ""),
                        "agent_id": summary.get("agent_id", ""),
                        "status": summary.get("status", ""),
                        "updated_at": summary.get("updated_at", ""),
                        "active": candidate_id in active_session_ids,
                    },
                    "score": score,
                    "hits": hits[: min(max_items, 5)],
                }
            )
        groups.sort(key=lambda item: (int(item.get("score") or 0), str(item.get("session", {}).get("updated_at") or "")), reverse=True)
        return groups[:max_items]

    def session_summary(self, session_id: str) -> dict[str, Any]:
        normalized = str(session_id or "").strip()
        if not normalized:
            return {}
        for item in self._read_index().get("sessions", []):
            if isinstance(item, dict) and str(item.get("session_id") or "") == normalized:
                return dict(item)
        if self.exists(normalized):
            return self._read_state(normalized)
        return {}

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
            "active_key": state.get("active_key", ""),
            "generation": state.get("generation", 1),
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

    def _find_active_binding(self, active_key: str) -> dict[str, Any] | None:
        for binding in self._active_bindings():
            if str(binding.get("key") or "") == active_key:
                return binding
        return None

    def _upsert_active_binding(
        self,
        *,
        active_key: str,
        session_id: str,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
        generation: int,
    ) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        now = _now()
        existing = self._find_active_binding(active_key)
        created_at = str(existing.get("created_at") or now) if existing else now
        binding = {
            "key": active_key,
            "session_id": session_id,
            "channel": channel,
            "channel_account_id": channel_account_id,
            "peer_type": peer_type,
            "peer_id": peer_id,
            "agent_id": agent_id,
            "generation": generation,
            "created_at": created_at,
            "updated_at": now,
        }
        bindings = [item for item in self._active_bindings() if str(item.get("key") or "") != active_key]
        bindings.insert(0, binding)
        _write_json(self._active_path, {"schema_version": 1, "bindings": bindings})

    def _active_bindings(self) -> list[dict[str, Any]]:
        if not self._active_path.exists():
            return []
        payload = _read_json(self._active_path)
        bindings = payload.get("bindings") if isinstance(payload, dict) else []
        if isinstance(bindings, dict):
            return [
                {"key": str(key), **value}
                for key, value in bindings.items()
                if isinstance(value, dict)
            ]
        if not isinstance(bindings, list):
            return []
        return [item for item in bindings if isinstance(item, dict)]

    def _active_session_ids(self) -> set[str]:
        return {
            session_id
            for session_id in (str(item.get("session_id") or "").strip() for item in self._active_bindings())
            if session_id
        }

    def _related_session_summaries(self, current: dict[str, Any]) -> list[dict[str, Any]]:
        active_key = str(current.get("active_key") or "").strip()
        channel = str(current.get("channel") or "").strip()
        channel_account_id = str(current.get("channel_account_id") or "").strip()
        peer_type = str(current.get("peer_type") or "").strip()
        peer_id = str(current.get("peer_id") or "").strip()
        agent_id = str(current.get("agent_id") or "").strip()
        related: list[dict[str, Any]] = []
        for item in self._read_index().get("sessions", []):
            if not isinstance(item, dict):
                continue
            if active_key and str(item.get("active_key") or "").strip() == active_key:
                related.append(item)
                continue
            if (
                channel
                and str(item.get("channel") or "").strip() == channel
                and str(item.get("channel_account_id") or "").strip() == channel_account_id
                and str(item.get("peer_type") or "").strip() == peer_type
                and str(item.get("peer_id") or "").strip() == peer_id
                and str(item.get("agent_id") or "").strip() == agent_id
            ):
                related.append(item)
        related.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return related

    def _summary_matches_identity(
        self,
        summary: dict[str, Any],
        *,
        active_key: str,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
    ) -> bool:
        if active_key and str(summary.get("active_key") or "").strip() == active_key:
            return True
        return (
            str(summary.get("channel") or "").strip() == channel
            and str(summary.get("channel_account_id") or "").strip() == channel_account_id
            and str(summary.get("peer_type") or "").strip() == peer_type
            and str(summary.get("peer_id") or "").strip() == peer_id
            and str(summary.get("agent_id") or "").strip() == agent_id
        )

    def _new_session_id(
        self,
        *,
        channel: str,
        channel_account_id: str,
        peer_type: str,
        peer_id: str,
        agent_id: str,
    ) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        random_part = uuid.uuid4().hex[:8]
        raw_parts = [channel, channel_account_id, peer_type, peer_id, agent_id, timestamp, random_part]
        safe_parts = [_safe_part(part) for part in raw_parts[:-2]]
        digest = hashlib.sha1(":".join(raw_parts).encode("utf-8")).hexdigest()[:10]
        return "_".join(["sess", *safe_parts, timestamp, digest])

    def _session_dir(self, session_id: str) -> Path:
        return self._sessions_dir / session_id

    @property
    def _index_path(self) -> Path:
        return self._sessions_dir / "index.json"

    @property
    def _active_path(self) -> Path:
        return self._sessions_dir / "active.json"


def _safe_part(value: str) -> str:
    safe = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return safe[:80] or "unknown"


def _safe_filename(value: str) -> str:
    safe = _SAFE_ID_RE.sub("_", Path(value or "").name).strip("._-")
    return safe[:120] or "attachment"


def _select_session_summary(summaries: list[dict[str, Any]], selector: str) -> dict[str, Any] | None:
    normalized = str(selector or "").strip()
    if not normalized:
        return None
    if normalized.isdigit():
        index = int(normalized) - 1
        if 0 <= index < len(summaries):
            return summaries[index]
    exact = [
        item
        for item in summaries
        if str(item.get("session_id") or "").strip() == normalized
    ]
    if len(exact) == 1:
        return exact[0]
    prefix = [
        item
        for item in summaries
        if str(item.get("session_id") or "").strip().startswith(normalized)
    ]
    if len(prefix) == 1:
        return prefix[0]
    return None


def _message_search_score(message: dict[str, Any], query: str) -> int:
    content = str(message.get("content") or "")
    normalized_content = content.lower()
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return 1
    score = 0
    if normalized_query in normalized_content:
        score += 10 + normalized_content.count(normalized_query)
    terms = _search_terms(normalized_query)
    for term in terms:
        if term in normalized_content:
            score += 2 + normalized_content.count(term)
    return score


def _search_terms(query: str) -> list[str]:
    terms: list[str] = []
    for term in re.split(r"\s+", query):
        if _is_search_term(term):
            terms.append(term)
    try:
        import jieba
    except Exception:
        jieba = None
    if jieba is not None:
        for term in jieba.cut(query):
            normalized = str(term or "").strip().lower()
            if _is_search_term(normalized):
                terms.append(normalized)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped


def _is_search_term(term: str) -> bool:
    if not term:
        return False
    if len(term) >= 2:
        return True
    return bool(re.fullmatch(r"[a-zA-Z0-9]", term))


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
