import os
import shlex
import shutil
import subprocess
from pathlib import Path


class UpdateAlreadyRunningError(Exception):
    """Raised when a service update is already in progress."""


class SystemUpdateService:
    def __init__(self, project_root: Path, workspace: Path) -> None:
        self.project_root = project_root
        self.workspace = workspace
        self.run_dir = workspace / ".run"
        self.lock_path = self.run_dir / "update-service.lock"
        self.log_path = self.run_dir / "update-service.log"
        self.script_path = project_root / "run-prod.sh"

    def start_update(self) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError as exc:
            raise UpdateAlreadyRunningError("service update already running") from exc

        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(f"{os.getpid()}\n")

        command = (
            "set -e; "
            f"trap 'rm -f {shlex.quote(str(self.lock_path))}' EXIT; "
            f"cd {shlex.quote(str(self.project_root))}; "
            f"{shlex.quote(str(self.script_path))} "
            f"--workspace {shlex.quote(str(self.workspace))} "
            f">>{shlex.quote(str(self.log_path))} 2>&1"
        )
        try:
            self._start_background_command(command)
        except Exception:
            self.lock_path.unlink(missing_ok=True)
            raise

        return self.log_path

    def _start_background_command(self, command: str) -> None:
        if shutil.which("systemd-run"):
            subprocess.Popen(
                [
                    "systemd-run",
                    "--unit",
                    "super-personal-platform-update",
                    "--collect",
                    "/bin/sh",
                    "-c",
                    command,
                ],
                cwd=self.project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

        subprocess.Popen(
            ["/bin/sh", "-c", command],
            cwd=self.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
