import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentRunStateService:
    def __init__(self, workspace: Path) -> None:
        self._dir = workspace / "agent_runs"

    def new_run_id(self) -> str:
        return f"run-{uuid.uuid4().hex[:12]}"

    def start_run(self, *, session_id: str, run_id: str, agent_id: str) -> dict[str, Any]:
        now = _now_iso()
        data = {
            "schema_version": RUN_SCHEMA_VERSION,
            "id": run_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "intent": None,
            "goal": None,
            "phase": None,
            "evidence": [],
            "candidate": None,
            "verification": None,
            "final_response": "",
            "error": None,
        }
        self._write(session_id, run_id, data)
        return data

    def update_run(
        self,
        *,
        session_id: str,
        run_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        data = self.get_run(session_id=session_id, run_id=run_id)
        data.update(fields)
        data["updated_at"] = _now_iso()
        self._write(session_id, run_id, data)
        return data

    def get_run(self, *, session_id: str, run_id: str) -> dict[str, Any]:
        path = self._run_path(session_id, run_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _run_path(self, session_id: str, run_id: str) -> Path:
        return self._dir / session_id / f"{run_id}.json"

    def _write(self, session_id: str, run_id: str, data: dict[str, Any]) -> None:
        path = self._run_path(session_id, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
