"""Native file access to existing local shared text knowledge."""
from pathlib import Path
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import DeleteResult, FileUploadResponse
from server.infrastructure.agent_filesystem_backend import AgentFilesystemBackend


class SharedFilesBackend(AgentFilesystemBackend):
    def _resolve_path(self, key: str) -> Path:
        if not key.startswith('/') or '\\' in key or any(p in {'.', '..'} for p in key.split('/')):
            raise PermissionError('Invalid shared knowledge path')
        raw = self.cwd / key.lstrip('/')
        if any(p.is_symlink() for p in (raw, *raw.parents)):
            raise PermissionError('Shared knowledge symlinks are not accessible')
        return FilesystemBackend._resolve_path(self, key)

    def _mutation_error(self, file_path, *, protect_root=False):
        try:
            path = self._resolve_path(file_path)
            if path == self.cwd or path.suffix.lower() not in {'.md', '.txt', '.json', '.jsonl'}:
                return 'Only shared text documents may be written'
        except (ValueError, OSError, RuntimeError) as exc:
            return str(exc)
        return None

    def delete(self, file_path):
        return DeleteResult(error='Shared knowledge deletion is not supported')

    def upload_files(self, files):
        return [FileUploadResponse(path=path, error='permission_denied') for path, _ in files]
