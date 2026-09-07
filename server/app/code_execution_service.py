from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from server.infrastructure.config import CodeExecutionConfig
from server.infrastructure.docker_gvisor_sandbox import CodeSandboxError, DockerGVisorSandbox


class CodeExecutionService:
    def __init__(self, workspace: Path, config: CodeExecutionConfig) -> None:
        self._workspace = workspace
        self._config = config
        self._sandbox = DockerGVisorSandbox(config)

    async def execute(
        self,
        *,
        language: str,
        code: str,
        agent_id: str,
        run_id: str,
        files: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        if not self._config.enabled:
            return _error("CodeExecutionDisabledError", "code_execution.enabled is false")
        capability = await self._sandbox.capability_check()
        if not capability.get("ok"):
            return {"ok": False, "tool": "execute_code", "recoverable": True, **capability}

        execution_id = f"exec_{uuid.uuid4().hex[:12]}"
        execution_root = self._workspace / "code_runs" / str(run_id or "manual")
        try:
            result = await self._sandbox.execute(
                language=language,
                code=code,
                execution_root=execution_root,
                files=files,
            )
        except CodeSandboxError as exc:
            return _error(exc.__class__.__name__, str(exc))

        artifact_root = self._artifact_root(agent_id=agent_id, run_id=run_id, execution_id=execution_id)
        artifact_files = []
        for file in result.files:
            target = artifact_root / file.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file.path, target)
            artifact_files.append(
                {
                    "path": f"/artifacts/code_runs/{run_id}/{execution_id}/{file.relative_path}",
                    "mime": file.mime,
                    "size": file.size,
                }
            )

        payload: dict[str, Any] = {
            "ok": result.ok,
            "tool": "execute_code",
            "language": result.language,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "files": artifact_files,
            "duration_ms": result.duration_ms,
            "runtime": "docker_gvisor",
            "docker": capability,
        }
        if result.error:
            payload["error"] = result.error
            payload["recoverable"] = True
        return payload

    def execute_sync(self, **kwargs: Any) -> str:
        from server.app.webdav_context_service import run_async

        return json.dumps(run_async(self.execute(**kwargs)), ensure_ascii=False)

    def _artifact_root(self, *, agent_id: str, run_id: str, execution_id: str) -> Path:
        safe_agent_id = _safe_segment(agent_id or "default")
        safe_run_id = _safe_segment(run_id or "manual")
        safe_execution_id = _safe_segment(execution_id)
        return self._workspace / "agents" / safe_agent_id / "artifacts" / "code_runs" / safe_run_id / safe_execution_id


def _error(error_type: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": "execute_code",
        "recoverable": True,
        "error": {"type": error_type, "message": message},
        "message": "execute_code could not run. Treat this as a tool observation and choose a fallback.",
    }


def _safe_segment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value or "").strip())
    return cleaned.strip("._-")[:80] or "default"
