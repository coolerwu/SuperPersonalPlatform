from __future__ import annotations

import json
import tempfile
import shutil
import uuid
from pathlib import Path
from typing import Any

from server.infrastructure.config import CodeExecutionConfig
from server.infrastructure.agent_workspace import agent_workspace_path, workspace_member_path
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
        language = language.strip().lower()
        if language not in self._config.languages or language not in {"python", "shell"} or not code.strip():
            return _error("CodeSandboxError", "A supported language and non-empty code are required")
        execution_id = f"exec_{uuid.uuid4().hex}"
        workspace = agent_workspace_path(self._workspace, agent_id or "default")
        suffix = ".py" if language.strip().lower() == "python" else ".sh"
        script_path = workspace_member_path(workspace, "scratch", f"{execution_id}{suffix}")
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(code, encoding="utf-8")
        capability = await self._sandbox.capability_check()
        if not capability.get("ok"):
            return {"ok": False, "tool": "execute_code", "recoverable": True, **capability, "script_path": f"/scratch/{script_path.name}"}
        execution_root = Path(tempfile.mkdtemp(prefix="spp-exec-"))
        preserve_outputs = False
        artifact_files = []
        try:
            result = await self._sandbox.execute(
                language=language,
                code=code,
                execution_root=execution_root,
                files=files,
            )
            try:
                artifact_root = workspace_member_path(workspace, "artifacts", execution_id)
                for file in result.files:
                    target = workspace_member_path(workspace, "artifacts", execution_id, file.relative_path)
                    if not target.resolve().is_relative_to(artifact_root.resolve()):
                        raise OSError("Sandbox output escapes artifact directory")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file.path, target)
                    artifact_files.append({
                        "path": f"/artifacts/{execution_id}/{file.relative_path}",
                        "mime": file.mime,
                        "size": file.size,
                    })
            except (OSError, ValueError) as exc:
                preserve_outputs = True
                return {
                    **_error("ArtifactCollectionError", str(exc)),
                    "script_path": f"/scratch/{script_path.name}",
                    "files": artifact_files,
                    "recovery_path": str(execution_root),
                }
        except CodeSandboxError as exc:
            preserve_outputs = getattr(exc, "preserve_execution", False)
            payload = {**_error(exc.__class__.__name__, str(exc)), "script_path": f"/scratch/{script_path.name}"}
            if preserve_outputs:
                payload["recovery_path"] = str(execution_root)
            return payload
        finally:
            if not preserve_outputs:
                shutil.rmtree(execution_root)

        payload: dict[str, Any] = {
            "ok": result.ok,
            "script_path": f"/scratch/{script_path.name}",
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


def _error(error_type: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": "execute_code",
        "recoverable": True,
        "error": {"type": error_type, "message": message},
        "message": "execute_code could not run. Treat this as a tool observation and choose a fallback.",
    }
