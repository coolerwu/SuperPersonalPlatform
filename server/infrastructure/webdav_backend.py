"""Agent-scoped WebDAV view shared by file tools and Context tools."""
import asyncio
from dataclasses import replace
from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import DeleteResult, EditResult, FileUploadResponse, WriteResult

from server.app.webdav_context_service import WebDAVContextService, run_async
from server.infrastructure.agent_filesystem_backend import AgentFilesystemBackend
from server.infrastructure.agent_workspace import WebDAVPathPolicy


class AgentWebDAVView:
    def __init__(self, service: WebDAVContextService, policy: WebDAVPathPolicy):
        self.service = service
        self.policy = policy

    async def refresh_if_stale(self):
        await self.service.refresh_if_stale()

    def documents(self):
        return [replace(item, path=path) for item in self.service.documents()
                if (path := self.policy.visible_path(item.path)) is not None]

    def recent_documents(self, *, limit=5):
        # Filter before limiting so other Agent mappings cannot hide recent notes.
        return [replace(item, path=path) for item in self.service.recent_documents(limit=None)
                if (path := self.policy.visible_path(item.path)) is not None][:limit]

    def _global_path(self, path, *, write=False):
        if not path.startswith("/webdav/"):
            raise ValueError("Expected /webdav/... path")
        return self.policy.resolve(path[len("/webdav"):], write=write)

    async def write(self, *, absolute_path, content, mode="append"):
        result = await self.service.write(absolute_path=self._global_path(absolute_path, write=True), content=content, mode=mode)
        return {**result, "path": absolute_path}

    async def edit(self, *, absolute_path, old_string, new_string, replace_all=False):
        result = await self.service.edit(absolute_path=self._global_path(absolute_path, write=True), old_string=old_string, new_string=new_string, replace_all=replace_all)
        return {**result, "path": absolute_path}


class WebDAVFilesystemBackend(AgentFilesystemBackend):
    def __init__(self, view: AgentWebDAVView):
        self.view = view
        # No per-Agent copies or aliases. Guard every access against the shared cache root.
        root = view.service._files_dir / view.policy.config.path.lstrip("/")
        super().__init__(root_dir=root, virtual_mode=True)

    def _resolve_path(self, key: str) -> Path:
        global_path = self.view.policy.resolve(key)
        self.view.service._cache_file_path(global_path) if key != "/" else self._check_root()
        return FilesystemBackend._resolve_path(self, key)

    def _check_root(self):
        root = self.view.service._files_dir
        path = root / self.view.policy.config.path.lstrip("/")
        if not path.resolve().is_relative_to(root.resolve()) or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(self.view.service._cache_dir)):
            raise PermissionError("WebDAV cache contains a symlink")

    async def agrep(self, pattern, path=None, glob=None, *, max_count=None, context_lines=0):
        return await asyncio.to_thread(self.grep, pattern, path, glob, max_count=max_count, context_lines=context_lines)

    def write(self, file_path, content):
        try:
            result = run_async(self.view.write(absolute_path="/webdav" + file_path, content=content, mode="create"))
            return WriteResult(path=file_path)
        except Exception as exc:
            return WriteResult(error=str(exc))

    def edit(self, file_path, old_string, new_string, replace_all=False):
        try:
            result = run_async(self.view.edit(absolute_path="/webdav" + file_path, old_string=old_string, new_string=new_string, replace_all=replace_all))
            return EditResult(path=file_path, occurrences=result["occurrences"])
        except Exception as exc:
            return EditResult(error=str(exc))

    def delete(self, file_path):
        return DeleteResult(error="WebDAV deletion is not supported; the mount root is protected")

    def upload_files(self, files):
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]
