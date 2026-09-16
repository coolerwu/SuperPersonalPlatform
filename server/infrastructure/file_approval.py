"""Persisted, exact-file approval leases; never change filesystem permissions."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable


def approval_file(tool: str, args: dict) -> str | None:
    path = args.get("file_path")
    if tool not in {"write_file", "edit_file"} or not isinstance(path, str):
        return None
    if not path.startswith("/webdav/") or path.endswith("/") or "\\" in path or "\0" in path:
        return None
    if any(part in {"", ".", ".."} for part in path.split("/")[1:]):
        return None
    return path


class FileApprovalStore:
    def __init__(self, path: Path, agent_id: str):
        self.path = path
        self.agent_id = agent_id

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def grant(self, file_path: str) -> dict:
        now = time.time()
        grant = {"agent_id": self.agent_id, "file_path": file_path, "approved_at": now, "expires_at": now + 600}
        records = [r for r in self._read() if isinstance(r, dict) and isinstance(r.get("expires_at"), (int, float))
                   and r["expires_at"] > now and (r.get("agent_id"), r.get("file_path")) != (self.agent_id, file_path)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps([*records, grant], ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)
        return grant

    def allows(self, tool: str, args: dict) -> bool:
        path = approval_file(tool, args)
        if path is None:
            return False
        now = time.time()
        return any(isinstance(r, dict) and r.get("agent_id") == self.agent_id and r.get("file_path") == path
                   and isinstance(r.get("expires_at"), (int, float)) and r["expires_at"] > now
                   for r in self._read())

    def wrap(self, tool: str, original: Callable) -> Callable:
        def when(request):
            # Evaluate live time for every call, including calls in long-running graphs.
            return original(request) and not self.allows(tool, request.tool_call.get("args", {}))
        return when
