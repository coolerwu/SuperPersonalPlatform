from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


class InvalidLogFileError(Exception):
    pass


@dataclass(frozen=True)
class LogFileSummary:
    name: str
    path: str
    size: int
    modified_at: str


@dataclass(frozen=True)
class LogFileContent:
    name: str
    path: str
    size: int
    modified_at: str
    content: str
    truncated: bool


class SystemLogService:
    def __init__(
        self,
        workspace: Path,
        retention_days: int = 3,
        tail_bytes: int = 200 * 1024,
    ) -> None:
        self.logs_dir = workspace / "logs"
        self.retention_days = retention_days
        self.tail_bytes = tail_bytes

    def current_log_path(self) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.logs_dir / f"platform-{today}.log"

    def append_request_log(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client: str,
    ) -> None:
        self._ensure_logs_dir()
        self.cleanup_old_logs()
        timestamp = datetime.now().isoformat(timespec="seconds")
        line = (
            f"{timestamp} request method={method} path={path} "
            f"status={status_code} duration_ms={duration_ms:.1f} client={client}\n"
        )
        with self.current_log_path().open("a", encoding="utf-8") as log_file:
            log_file.write(line)

    def append_line(self, text: str) -> None:
        self._ensure_logs_dir()
        self.cleanup_old_logs()
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self.current_log_path().open("a", encoding="utf-8") as log_file:
            log_file.write(f"{timestamp} {text}\n")

    def list_logs(self) -> list[LogFileSummary]:
        self._ensure_logs_dir()
        self.cleanup_old_logs()
        files = sorted(
            self.logs_dir.glob("platform-*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [self._summary(path) for path in files]

    def read_log(self, name: str) -> LogFileContent:
        self._ensure_logs_dir()
        self.cleanup_old_logs()
        path = self._resolve_log_path(name)
        if not path.exists():
            raise FileNotFoundError(name)
        if not path.is_file():
            raise InvalidLogFileError("log target is not a file")

        size = path.stat().st_size
        truncated = size > self.tail_bytes
        with path.open("rb") as log_file:
            if truncated:
                log_file.seek(-self.tail_bytes, 2)
            content = log_file.read().decode("utf-8", errors="replace")

        summary = self._summary(path)
        return LogFileContent(
            name=summary.name,
            path=summary.path,
            size=summary.size,
            modified_at=summary.modified_at,
            content=content,
            truncated=truncated,
        )

    def cleanup_old_logs(self) -> None:
        if not self.logs_dir.exists():
            return
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        cutoff_timestamp = cutoff.timestamp()
        for path in self.logs_dir.glob("platform-*.log"):
            if path.is_file() and path.stat().st_mtime < cutoff_timestamp:
                path.unlink()

    def _ensure_logs_dir(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_log_path(self, name: str) -> Path:
        if Path(name).name != name or not name.startswith("platform-") or not name.endswith(".log"):
            raise InvalidLogFileError("invalid log file name")
        return self.logs_dir / name

    def _summary(self, path: Path) -> LogFileSummary:
        stat = path.stat()
        return LogFileSummary(
            name=path.name,
            path=str(path),
            size=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        )
