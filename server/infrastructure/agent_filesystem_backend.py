from __future__ import annotations

from pathlib import PurePosixPath

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import DeleteResult, EditResult, FileUploadResponse, WriteResult


AGENT_WORKSPACE_DIRECTORIES = (
    "artifacts",
    "improvements",
    "meditations",
    "memories",
    "notes",
    "scratch",
    "skills",
)


class AgentFilesystemBackend(FilesystemBackend):
    """Filesystem backend that limits mutations to declared agent directories."""

    def write(self, file_path: str, content: str) -> WriteResult:
        error = self._mutation_error(file_path)
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
        return super().edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        error = self._mutation_error(file_path, protect_root=True)
        if error:
            return DeleteResult(error=error)
        return super().delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for file_path, content in files:
            error = self._mutation_error(file_path)
            if error:
                responses.append(FileUploadResponse(path=file_path, error=error))
            else:
                responses.extend(super().upload_files([(file_path, content)]))
        return responses

    def _mutation_error(self, file_path: str, *, protect_root: bool = False) -> str | None:
        normalized = str(file_path or "").strip().replace("\\", "/")
        virtual_path = PurePosixPath("/" + normalized.lstrip("/"))
        parts = virtual_path.parts
        top_level = parts[1] if len(parts) > 1 else ""
        allowed = top_level in AGENT_WORKSPACE_DIRECTORIES
        invalid_path = ".." in parts or normalized.startswith("~")
        if allowed and not invalid_path and not (protect_root and len(parts) == 2):
            try:
                super()._resolve_path(file_path)
            except (OSError, RuntimeError, ValueError):
                invalid_path = True
            else:
                return None

        allowed_paths = ", ".join(f"/{name}/" for name in AGENT_WORKSPACE_DIRECTORIES)
        if invalid_path:
            reason = "The requested path is invalid or escapes the agent workspace."
        elif top_level == "workspace":
            reason = "The virtual '/' is already this agent's workspace; do not create another /workspace directory."
        elif protect_root and allowed:
            reason = f"The managed root '/{top_level}/' cannot be deleted."
        else:
            reason = "The requested path is outside the writable agent directories."
        return f"Permission denied for '{file_path}'. {reason} Writable directories: {allowed_paths}"
