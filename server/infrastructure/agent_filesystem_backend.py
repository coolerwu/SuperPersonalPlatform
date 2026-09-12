from __future__ import annotations

import os
import time
from pathlib import Path, PurePosixPath

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    DeleteResult, EditResult, FileUploadResponse, GlobResult, GrepResult,
    LsResult, ReadResult, WriteResult,
)
from deepagents.backends.utils import compile_grep_include_glob

from server.infrastructure.agent_workspace import AGENT_WORKSPACE_DIRECTORIES


SKILL_CONTRACT_START = "<!-- BEGIN USER CONTRACT -->"
SKILL_CONTRACT_END = "<!-- END USER CONTRACT -->"
SKILL_CONTRACT_ERROR = (
    "This skill contains a protected user contract. Agent self-improvement may add guidance around it, "
    "but cannot change, remove, overwrite, or delete the protected block. Record the proposed contract "
    "change under /improvements/ instead."
)


class AgentFilesystemBackend(FilesystemBackend):
    """Restrict all Agent file operations to declared, non-browser directories."""

    def _resolve_path(self, key: str) -> Path:
        path = super()._resolve_path(key)
        relative = path.relative_to(self.cwd)
        if relative.parts and relative.parts[0] not in AGENT_WORKSPACE_DIRECTORIES:
            raise PermissionError("Path is outside Agent-accessible directories")
        # Never follow an alias into a managed browser profile, even within cwd.
        raw = self.cwd / str(key).lstrip("/")
        if any(p.is_symlink() for p in (raw, *raw.parents) if p != self.cwd and p.is_relative_to(self.cwd)):
            raise PermissionError("Symlinks are not accessible through Agent file tools")
        return path

    def read(self, file_path, offset=0, limit=2000):
        try:
            return super().read(file_path, offset, limit)
        except (ValueError, OSError, RuntimeError):
            return ReadResult(error="Permission denied or invalid Agent path")

    def ls(self, path):
        try:
            result = super().ls(path)
            if result.entries:
                result.entries = [entry for entry in result.entries if self._visible(entry["path"])]
            return result
        except (ValueError, OSError, RuntimeError):
            return LsResult(error="Permission denied or invalid Agent path", entries=[])

    def _visible(self, path: str) -> bool:
        try:
            self._resolve_path(path)
            return True
        except (ValueError, OSError, RuntimeError):
            return False

    def _search_files(self, base: Path):
        if base.is_file():
            yield base
            return
        for current, directories, files in os.walk(base, followlinks=False):
            directories[:] = [name for name in directories if self._visible(self._to_virtual_path(Path(current) / name))]
            for name in files:
                candidate = Path(current) / name
                if self._visible(self._to_virtual_path(candidate)):
                    yield candidate

    def grep(self, pattern, path=None, glob=None, *, max_count=None, context_lines=0):
        if context_lines < 0:
            raise ValueError("context_lines must be non-negative")
        matches = []
        try:
            base = self._resolve_path(path or "/")
            matcher = compile_grep_include_glob(glob) if glob else lambda _: True
            deadline = time.monotonic() + 10
            for file in self._search_files(base):
                if time.monotonic() > deadline:
                    return GrepResult(matches=matches, truncated=True)
                relative = file.name if base.is_file() else file.relative_to(base).as_posix()
                if not matcher(relative):
                    continue
                # Go through guarded download rather than reading an unchecked search result.
                response = self.download_files([self._to_virtual_path(file)])[0]
                if response.error or response.content is None:
                    continue
                try:
                    lines = response.content.decode("utf-8").splitlines()
                except UnicodeDecodeError:
                    continue
                for number, line in enumerate(lines, 1):
                    if pattern not in line:
                        continue
                    if max_count is not None and len(matches) >= max_count:
                        return GrepResult(matches=matches, truncated=True)
                    match = {"path": self._to_virtual_path(file), "line": number, "text": line}
                    if context_lines:
                        match["context_before"] = [{"line": i + 1, "text": lines[i]} for i in range(max(0, number - 1 - context_lines), number - 1)]
                        match["context_after"] = [{"line": i + 1, "text": lines[i]} for i in range(number, min(len(lines), number + context_lines))]
                    matches.append(match)
            return GrepResult(matches=matches)
        except (ValueError, OSError, RuntimeError) as exc:
            return GrepResult(matches=matches, error=str(exc))

    def glob(self, pattern, path=None):
        matches = []
        try:
            base = self._resolve_path(path or "/")
            matcher = compile_grep_include_glob(pattern)
            deadline = time.monotonic() + 10
            for file in self._search_files(base):
                if time.monotonic() > deadline:
                    return GlobResult(matches=matches, truncated=True, truncation_reason="budget")
                relative = file.name if base.is_file() else file.relative_to(base).as_posix()
                if matcher(relative):
                    matches.append({"path": self._to_virtual_path(file), "is_dir": False, "size": file.stat().st_size})
            return GlobResult(matches=matches)
        except (ValueError, OSError, RuntimeError) as exc:
            return GlobResult(matches=matches, error=str(exc))

    def write(self, file_path: str, content: str) -> WriteResult:
        error = self._mutation_error(file_path)
        if error:
            return WriteResult(error=error)
        error = self._skill_contract_write_error(file_path, content)
        if error:
            return WriteResult(error=error)
        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        error = self._mutation_error(file_path)
        if error:
            return EditResult(error=error)
        error = self._skill_contract_edit_error(file_path, old_string, new_string, replace_all)
        if error:
            return EditResult(error=error)
        return super().edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        error = self._mutation_error(file_path, protect_root=True)
        if error:
            return DeleteResult(error=error)
        error = self._skill_contract_delete_error(file_path)
        if error:
            return DeleteResult(error=error)
        return super().delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for file_path, content in files:
            error = self._mutation_error(file_path)
            if not error:
                try:
                    next_content = content.decode("utf-8")
                except UnicodeDecodeError:
                    next_content = ""
                error = self._skill_contract_write_error(file_path, next_content)
            if error:
                responses.append(FileUploadResponse(path=file_path, error=error))
            else:
                responses.extend(super().upload_files([(file_path, content)]))
        return responses

    def _skill_contract_write_error(self, file_path: str, next_content: str) -> str | None:
        path = self._resolve_path(file_path)
        if not self._is_skill_file(path) or not path.is_file():
            return None
        try:
            current_content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        current_contract = _skill_contract(current_content)
        if current_contract is None:
            return SKILL_CONTRACT_ERROR if SKILL_CONTRACT_START in current_content else None
        return None if _skill_contract(next_content) == current_contract else SKILL_CONTRACT_ERROR

    def _skill_contract_edit_error(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
    ) -> str | None:
        path = self._resolve_path(file_path)
        if not self._is_skill_file(path) or not path.is_file() or not old_string:
            return None
        try:
            current_content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if SKILL_CONTRACT_START not in current_content:
            return None
        next_content = current_content.replace(old_string, new_string, -1 if replace_all else 1)
        return self._skill_contract_write_error(file_path, next_content)

    def _skill_contract_delete_error(self, file_path: str) -> str | None:
        path = self._resolve_path(file_path)
        candidates = [path] if path.is_file() else list(path.glob("**/SKILL.md")) if path.is_dir() else []
        for candidate in candidates:
            if not self._is_skill_file(candidate):
                continue
            try:
                content = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if SKILL_CONTRACT_START in content:
                return SKILL_CONTRACT_ERROR
        return None

    def _is_skill_file(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.cwd)
        except ValueError:
            return False
        return len(relative.parts) >= 3 and relative.parts[0] == "skills" and relative.name == "SKILL.md"

    def _mutation_error(self, file_path: str, *, protect_root: bool = False) -> str | None:
        normalized = str(file_path or "").strip().replace("\\", "/")
        virtual_path = PurePosixPath("/" + normalized.lstrip("/"))
        parts = virtual_path.parts
        top_level = parts[1] if len(parts) > 1 else ""
        allowed = top_level in AGENT_WORKSPACE_DIRECTORIES
        invalid_path = ".." in parts or normalized.startswith("~")
        if allowed and not invalid_path and not (protect_root and len(parts) == 2):
            try:
                self._resolve_path(file_path)
            except (OSError, RuntimeError, ValueError):
                invalid_path = True
            else:
                return None

        allowed_paths = ", ".join(f"/{name}/" for name in AGENT_WORKSPACE_DIRECTORIES)
        if invalid_path:
            reason = "The requested path is invalid or escapes the agent workspace."
        elif top_level == "workspace":
            reason = "The virtual root is already your workspace; use /scratch/ or /artifacts/ directly."
        elif protect_root and allowed:
            reason = f"The managed root '/{top_level}/' cannot be deleted."
        else:
            reason = "The requested path is outside the writable agent directories."
        return f"Permission denied for '{file_path}'. {reason} Writable directories: {allowed_paths}"


def _skill_contract(content: str) -> str | None:
    start = content.find(SKILL_CONTRACT_START)
    if start < 0:
        return None
    end = content.find(SKILL_CONTRACT_END, start + len(SKILL_CONTRACT_START))
    if end < 0:
        return None
    end += len(SKILL_CONTRACT_END)
    return content[start:end]
