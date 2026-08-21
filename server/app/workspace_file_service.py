import shutil
from dataclasses import dataclass
from pathlib import Path


class InvalidWorkspacePathError(Exception):
    pass


class WorkspaceFileTooLargeError(Exception):
    pass


class WorkspaceFileNotTextError(Exception):
    pass


class ProtectedWorkspacePathError(Exception):
    pass


@dataclass(frozen=True)
class WorkspaceEntry:
    name: str
    path: str
    type: str
    size: int
    modified_at: float
    deletable: bool


@dataclass(frozen=True)
class WorkspaceTextFile:
    path: str
    size: int
    modified_at: float
    content: str
    editable: bool


class WorkspaceFileService:
    max_read_bytes = 1_000_000
    max_write_bytes = 1_000_000
    protected_root_files = {"config.yaml"}
    protected_root_directories = {"agents", "context", "runs", "sessions", "channels", "logs"}

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def list_entries(self, relative_path: str = "") -> list[WorkspaceEntry]:
        directory = self._resolve(relative_path)
        if not directory.exists():
            raise FileNotFoundError(relative_path)
        if not directory.is_dir():
            raise NotADirectoryError(relative_path)

        entries: list[WorkspaceEntry] = []
        for child in directory.iterdir():
            stat = child.stat()
            entries.append(
                WorkspaceEntry(
                    name=child.name,
                    path=self._relative(child),
                    type="directory" if child.is_dir() else "file",
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    deletable=self._is_deletable(child),
                )
            )
        return sorted(entries, key=lambda entry: (entry.type != "directory", entry.name.lower()))

    def read_text_file(self, relative_path: str) -> WorkspaceTextFile:
        path = self._resolve(relative_path)
        if not path.exists():
            raise FileNotFoundError(relative_path)
        if not path.is_file():
            raise IsADirectoryError(relative_path)

        stat = path.stat()
        if stat.st_size > self.max_read_bytes:
            raise WorkspaceFileTooLargeError(relative_path)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceFileNotTextError(relative_path) from exc

        return WorkspaceTextFile(
            path=self._relative(path),
            size=stat.st_size,
            modified_at=stat.st_mtime,
            content=content,
            editable=self._is_editable(path),
        )

    def write_text_file(self, relative_path: str, content: str) -> WorkspaceTextFile:
        path = self._resolve(relative_path)
        if not path.exists():
            raise FileNotFoundError(relative_path)
        if not path.is_file():
            raise IsADirectoryError(relative_path)
        if not self._is_editable(path):
            raise WorkspaceFileNotTextError(relative_path)
        if len(content.encode("utf-8")) > self.max_write_bytes:
            raise WorkspaceFileTooLargeError(relative_path)

        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
        return self.read_text_file(relative_path)

    def delete_path(self, relative_path: str) -> None:
        path = self._resolve(relative_path)
        if path == self.workspace:
            raise ProtectedWorkspacePathError(relative_path)
        if self._is_protected_relative(self._relative(path)):
            raise ProtectedWorkspacePathError(relative_path)
        if not path.exists():
            raise FileNotFoundError(relative_path)

        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink()

    def _resolve(self, relative_path: str) -> Path:
        normalized = str(relative_path or "").strip()
        if normalized in {"", "."}:
            return self.workspace
        path = Path(normalized)
        if path.is_absolute() or any(part in {"..", ""} for part in path.parts):
            raise InvalidWorkspacePathError(relative_path)
        candidate = (self.workspace / path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise InvalidWorkspacePathError(relative_path)
        return candidate

    def _relative(self, path: Path) -> str:
        value = path.resolve().relative_to(self.workspace)
        return value.as_posix()

    def _is_deletable(self, path: Path) -> bool:
        return not self._is_protected_relative(self._relative(path))

    def _is_protected_relative(self, relative: str) -> bool:
        if "/" in relative:
            return False
        return relative in self.protected_root_files or relative in self.protected_root_directories

    def _is_editable(self, path: Path) -> bool:
        return path.suffix.lower() in {
            ".json",
            ".jsonl",
            ".log",
            ".md",
            ".txt",
            ".yaml",
            ".yml",
        }
