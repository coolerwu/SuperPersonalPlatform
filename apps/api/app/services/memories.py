"""Markdown memory documents and Claude's auto-memory directory.

The user profile lives in ``claude/CLAUDE.md`` (user-level memory, shared by
every Agent). Each Agent additionally has a project-level ``CLAUDE.md`` in its
workspace, which the CLI loads because the runner sets the workspace as cwd and
enables the ``project`` setting source. Claude's own auto memory stays under
``claude/projects/<agent_id>/memory/`` and is only read here.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.claude_env import claude_paths, workspace_for
from app.services.identifiers import valid_agent_id
from app.services.settings_store import SettingsStore

MAX_AUTO_MEMORY_FILES = 200
MAX_AUTO_MEMORY_FILE_BYTES = 128 * 1024
MAX_AUTO_MEMORY_TOTAL_BYTES = 1024 * 1024
MAX_AUTO_MEMORY_SCAN = 2000
MAX_AUTO_MEMORY_ERRORS = 20


class AgentNotFoundError(LookupError):
    """The Agent id is malformed or no longer configured."""


class MemoryPathError(ValueError):
    """The memory file is not safe to read or write (for example a symlink)."""


@dataclass(frozen=True)
class AutoMemoryFile:
    name: str
    content: str
    size: int
    updated_at: str


@dataclass(frozen=True)
class AutoMemoryCatalog:
    directory: str
    files: tuple[AutoMemoryFile, ...] = ()
    errors: tuple[str, ...] = ()


def user_memory_path() -> Path:
    return claude_paths()["config"] / "CLAUDE.md"


def agent_memory_path(agent_id: str, *, home: Path | None = None) -> Path:
    return workspace_for(agent_id, home=home) / "CLAUDE.md"


def auto_memory_dir(agent_id: str, *, home: Path | None = None) -> Path:
    return claude_paths(home)["config"] / "projects" / agent_id / "memory"


async def require_agent(store: SettingsStore, agent_id: str) -> None:
    if not valid_agent_id(agent_id):
        raise AgentNotFoundError(agent_id)
    snapshot = await store.get()
    if not any(agent.id == agent_id for agent in snapshot.agents):
        raise AgentNotFoundError(agent_id)


def read_document(path: Path) -> str:
    if path.is_symlink():
        raise MemoryPathError("记忆文件不能是符号链接")
    try:
        return path.read_text("utf-8")
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError as exc:
        raise MemoryPathError("记忆文件不是有效的 UTF-8 文本") from exc


def write_document(path: Path, content: str) -> None:
    if path.is_symlink():
        raise MemoryPathError("记忆文件不能是符号链接")
    backup_and_write(path, content)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def backup_and_write(path: Path, content: str) -> None:
    if path.is_file():
        _atomic_write(
            path.with_name(path.name + ".bak"), path.read_text("utf-8")
        )
    _atomic_write(path, content)


def _cap_errors(errors: list[str]) -> tuple[str, ...]:
    if len(errors) <= MAX_AUTO_MEMORY_ERRORS:
        return tuple(errors)
    omitted = len(errors) - MAX_AUTO_MEMORY_ERRORS
    return tuple(
        errors[:MAX_AUTO_MEMORY_ERRORS] + [f"还有 {omitted} 条提示未显示"]
    )


def read_auto_memory(
    agent_id: str, *, home: Path | None = None
) -> AutoMemoryCatalog:
    directory = auto_memory_dir(agent_id, home=home)
    if directory.is_symlink():
        return AutoMemoryCatalog(
            directory=str(directory),
            errors=("auto memory 目录是符号链接，已跳过",),
        )
    if not directory.is_dir():
        return AutoMemoryCatalog(directory=str(directory))

    errors: list[str] = []
    try:
        entries = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_AUTO_MEMORY_SCAN:
                    errors.append(
                        f"目录文件超过 {MAX_AUTO_MEMORY_SCAN} 个，只扫描前 "
                        f"{MAX_AUTO_MEMORY_SCAN} 个"
                    )
                    break
                entries.append(entry)
    except OSError as exc:
        return AutoMemoryCatalog(
            directory=str(directory),
            errors=(f"无法读取 auto memory 目录：{exc}",),
        )
    entries.sort(key=lambda entry: (entry.name != "MEMORY.md", entry.name.casefold()))

    files: list[AutoMemoryFile] = []
    total = 0
    for entry in entries:
        if len(files) >= MAX_AUTO_MEMORY_FILES:
            errors.append(
                f"文件数量超过 {MAX_AUTO_MEMORY_FILES} 个，只展示前 "
                f"{MAX_AUTO_MEMORY_FILES} 个"
            )
            break
        name = entry.name
        if (
            Path(name).suffix.lower() != ".md"
            or name.startswith(".")
            or entry.is_symlink()
            or not entry.is_file(follow_symlinks=False)
        ):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError as exc:
            errors.append(f"跳过 {name}：{exc}")
            continue
        if stat.st_size > MAX_AUTO_MEMORY_FILE_BYTES:
            errors.append(
                f"跳过 {name}：超过单文件 "
                f"{MAX_AUTO_MEMORY_FILE_BYTES // 1024} KB 限制"
            )
            continue
        if total + stat.st_size > MAX_AUTO_MEMORY_TOTAL_BYTES:
            errors.append(
                f"跳过 {name}：超过总展示量 "
                f"{MAX_AUTO_MEMORY_TOTAL_BYTES // 1024} KB 限制"
            )
            continue
        try:
            content = Path(entry.path).read_text("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"跳过 {name}：{exc}")
            continue
        files.append(
            AutoMemoryFile(
                name=name,
                content=content,
                size=stat.st_size,
                updated_at=datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            )
        )
        total += stat.st_size
    return AutoMemoryCatalog(
        directory=str(directory),
        files=tuple(files),
        errors=_cap_errors(errors),
    )
