from __future__ import annotations

import asyncio
import json
import mimetypes
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.infrastructure.config import CodeExecutionConfig


class CodeSandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxOutputFile:
    path: Path
    relative_path: str
    mime: str
    size: int


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    language: str
    exit_code: int
    stdout: str
    stderr: str
    files: tuple[SandboxOutputFile, ...]
    duration_ms: int
    error: dict[str, str] | None = None


class DockerGVisorSandbox:
    def __init__(self, config: CodeExecutionConfig) -> None:
        self._config = config

    async def execute(
        self,
        *,
        language: str,
        code: str,
        execution_root: Path,
        files: tuple[dict[str, Any], ...] = (),
    ) -> SandboxResult:
        normalized_language = str(language or "").strip().lower()
        if normalized_language not in self._config.languages:
            raise CodeSandboxError(f"language is not enabled: {normalized_language}")
        if normalized_language not in {"python", "shell"}:
            raise CodeSandboxError("language must be python or shell")
        source = str(code or "")
        if not source.strip():
            raise CodeSandboxError("code is required")

        execution_id = f"exec_{uuid.uuid4().hex[:12]}"
        root = execution_root / execution_id
        input_dir = root / "input"
        work_dir = root / "work"
        output_dir = root / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        container_name = f"spp-code-{execution_id}"

        script_name = "main.py" if normalized_language == "python" else "script.sh"
        (work_dir / script_name).write_text(source, encoding="utf-8")
        _write_input_files(input_dir, files, max_file_bytes=self._config.max_file_bytes)

        command = self._docker_command(
            language=normalized_language,
            script_name=script_name,
            container_name=container_name,
            input_dir=input_dir,
            work_dir=work_dir,
            output_dir=output_dir,
        )
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise CodeSandboxError("docker executable was not found") from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            await _run_capture(("docker", "rm", "-f", container_name), timeout=10)
            duration_ms = int((time.monotonic() - started) * 1000)
            return SandboxResult(
                ok=False,
                language=normalized_language,
                exit_code=-1,
                stdout="",
                stderr="",
                files=(),
                duration_ms=duration_ms,
                error={
                    "type": "CodeExecutionTimeoutError",
                    "message": f"code execution exceeded {self._config.timeout_seconds} seconds",
                },
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = _decode_and_truncate(stdout_bytes, self._config.max_stdout_chars)
        stderr = _decode_and_truncate(stderr_bytes, self._config.max_stderr_chars)
        files_result = _collect_output_files(
            output_dir,
            max_files=self._config.max_files,
            max_file_bytes=self._config.max_file_bytes,
        )
        exit_code = int(process.returncode or 0)
        return SandboxResult(
            ok=exit_code == 0,
            language=normalized_language,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            files=files_result,
            duration_ms=duration_ms,
            error=None if exit_code == 0 else {"type": "CodeExecutionError", "message": f"process exited with code {exit_code}"},
        )

    async def capability_check(self) -> dict[str, Any]:
        docker = self._config.docker
        docker_path = shutil.which("docker")
        if not docker_path:
            return {"ok": False, "error": {"type": "DockerUnavailableError", "message": "docker executable was not found"}}
        runtimes = await _run_capture(("docker", "info", "--format", "{{json .Runtimes}}"), timeout=10)
        if runtimes["exit_code"] != 0:
            return {"ok": False, "error": {"type": "DockerUnavailableError", "message": runtimes["stderr"] or runtimes["stdout"]}}
        try:
            parsed_runtimes = json.loads(str(runtimes["stdout"] or "{}"))
        except json.JSONDecodeError:
            parsed_runtimes = {}
        if docker.runtime not in parsed_runtimes:
            return {
                "ok": False,
                "error": {"type": "GVisorRuntimeUnavailableError", "message": f"Docker runtime is not registered: {docker.runtime}"},
            }
        image = await _run_capture(("docker", "image", "inspect", docker.image), timeout=10)
        if image["exit_code"] != 0:
            return {
                "ok": False,
                "error": {"type": "DockerImageUnavailableError", "message": f"Docker image is not available: {docker.image}"},
            }
        return {"ok": True, "runtime": docker.runtime, "image": docker.image}

    def _docker_command(
        self,
        *,
        language: str,
        script_name: str,
        container_name: str,
        input_dir: Path,
        work_dir: Path,
        output_dir: Path,
    ) -> tuple[str, ...]:
        docker = self._config.docker
        if language == "python":
            inner_command = ("python", f"/workspace/work/{script_name}")
        else:
            inner_command = ("/bin/sh", f"/workspace/work/{script_name}")
        return (
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            f"--runtime={docker.runtime}",
            "--network=none",
            f"--cpus={docker.cpus}",
            f"--memory={docker.memory}",
            f"--pids-limit={docker.pids_limit}",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop=ALL",
            "--user",
            "1000:1000",
            "-v",
            f"{input_dir.resolve()}:/workspace/input:ro",
            "-v",
            f"{work_dir.resolve()}:/workspace/work:rw",
            "-v",
            f"{output_dir.resolve()}:/workspace/output:rw",
            "-w",
            "/workspace/work",
            docker.image,
            *inner_command,
        )


def _write_input_files(input_dir: Path, files: tuple[dict[str, Any], ...], *, max_file_bytes: int) -> None:
    for item in files:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or item.get("name") or "").strip()
        if not raw_path:
            continue
        relative = _safe_relative_path(raw_path)
        content = item.get("content")
        if content is None:
            continue
        encoded = str(content).encode("utf-8")
        if len(encoded) > max_file_bytes:
            raise CodeSandboxError(f"input file is larger than {max_file_bytes} bytes: {relative.as_posix()}")
        target = input_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)


def _safe_relative_path(value: str) -> Path:
    path = Path(value.lstrip("/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CodeSandboxError("input file paths must be relative and must not contain ..")
    return path


def _collect_output_files(output_dir: Path, *, max_files: int, max_file_bytes: int) -> tuple[SandboxOutputFile, ...]:
    if max_files <= 0:
        return ()
    root = output_dir.resolve()
    collected: list[SandboxOutputFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            continue
        size = resolved.stat().st_size
        if size > max_file_bytes:
            continue
        relative_path = resolved.relative_to(root).as_posix()
        mime = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        collected.append(SandboxOutputFile(path=resolved, relative_path=relative_path, mime=mime, size=size))
        if len(collected) >= max_files:
            break
    return tuple(collected)


def _decode_and_truncate(value: bytes, max_chars: int) -> str:
    text = value.decode("utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


async def _run_capture(command: tuple[str, ...], *, timeout: int) -> dict[str, Any]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"exit_code": 127, "stdout": "", "stderr": "docker executable was not found"}
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return {"exit_code": -1, "stdout": "", "stderr": "docker command timed out"}
    return {
        "exit_code": int(process.returncode or 0),
        "stdout": stdout.decode("utf-8", errors="replace").strip(),
        "stderr": stderr.decode("utf-8", errors="replace").strip(),
    }
