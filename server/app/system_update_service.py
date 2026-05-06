import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from server.app.system_log_service import SystemLogService


class UpdateAlreadyRunningError(Exception):
    """Raised when a service update is already in progress."""


@dataclass(frozen=True)
class UpdateLock:
    pid: int
    started_at: str


class SystemUpdateService:
    def __init__(
        self,
        project_root: Path,
        workspace: Path,
        log_service: SystemLogService | None = None,
    ) -> None:
        self.project_root = project_root
        self.workspace = workspace
        self.run_dir = workspace / ".run"
        self.lock_path = self.run_dir / "update-service.lock"
        self.log_service = log_service or SystemLogService(workspace)
        self.script_path = project_root / "run-prod.sh"

    def start_update(self) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_service.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_service.cleanup_old_logs()
        log_path = self.log_service.current_log_path()
        fd = self._create_lock()

        command = (
            "set +e; "
            f"trap 'rm -f {shlex.quote(str(self.lock_path))}' EXIT; "
            f"mkdir -p {shlex.quote(str(self.log_service.logs_dir))}; "
            "printf '\\n=== update-service started at %s ===\\n' \"$(date -Is)\" "
            f">>{shlex.quote(str(log_path))}; "
            f"cd {shlex.quote(str(self.project_root))}; "
            f"{shlex.quote(str(self.script_path))} "
            f"--workspace {shlex.quote(str(self.workspace))} "
            f">>{shlex.quote(str(log_path))} 2>&1; "
            "status=$?; "
            "printf '=== update-service finished at %s status=%s ===\\n' "
            f"\"$(date -Is)\" \"$status\" >>{shlex.quote(str(log_path))}; "
            "exit \"$status\""
        )
        try:
            process = self._start_background_command(command)
            self._write_lock(fd, process.pid)
        except Exception:
            os.close(fd)
            self.lock_path.unlink(missing_ok=True)
            raise

        return log_path

    def _start_background_command(self, command: str) -> subprocess.Popen:
        return subprocess.Popen(
            ["/bin/sh", "-c", command],
            cwd=self.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _create_lock(self) -> int:
        for _ in range(2):
            try:
                return os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
            except FileExistsError as exc:
                if self._lock_is_active():
                    raise UpdateAlreadyRunningError("service update already running") from exc
                self.lock_path.unlink(missing_ok=True)
        raise UpdateAlreadyRunningError("service update already running")

    def _write_lock(self, fd: int, pid: int) -> None:
        lock = UpdateLock(
            pid=pid,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            json.dump(lock.__dict__, lock_file)
            lock_file.write("\n")

    def _lock_is_active(self) -> bool:
        try:
            raw_content = self.lock_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return False
        if not raw_content:
            return False

        try:
            data = json.loads(raw_content)
            pid = int(data["pid"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return False

        return self._pid_is_running(pid)

    def _pid_is_running(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
