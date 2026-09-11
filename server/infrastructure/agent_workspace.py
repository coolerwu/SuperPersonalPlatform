"""Canonical Agent workspace paths and directory policy."""
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from server.domain.agent_config import AgentConfigError, AgentWebDAVConfig


@dataclass(frozen=True)
class WorkspaceDirectory:
    name: str
    purpose: str
    agent_access: bool = True


WORKSPACE_DIRECTORIES = (
    WorkspaceDirectory("artifacts", "Final deliverables; return these paths to the user."),
    WorkspaceDirectory("scratch", "Drafts and saved execution scripts (.py/.sh); subject to scratch retention cleanup."),
    WorkspaceDirectory("skills", "Reusable skills; follow Skills and SkillImprovement middleware rules."),
    WorkspaceDirectory("memories", "Long-term memory; follow MemoryMiddleware rules."),
    WorkspaceDirectory("improvements", "Skill reflections, reviews and change records."),
    WorkspaceDirectory("meditations", "Daily meditation records."),
    WorkspaceDirectory("browser", "Browser login state and cache; browser-service only. Never read, search or modify with file tools.", False),
)
AGENT_WORKSPACE_DIRECTORIES = tuple(d.name for d in WORKSPACE_DIRECTORIES if d.agent_access)


def agent_workspace_path(platform_workspace: Path, agent_id: str) -> Path:
    if not agent_id or agent_id in {".", ".."} or any(c in agent_id for c in ("/", "\\", "\0")):
        raise AgentConfigError("Agent ID must be a single path segment")
    root = platform_workspace.resolve()
    path = root / "agents" / agent_id / "workspace"
    if any(p.is_symlink() for p in (root / "agents", root / "agents" / agent_id, path)):
        raise AgentConfigError("Agent workspace cannot be a symlink")
    if not path.resolve().is_relative_to(root):
        raise AgentConfigError("Agent workspace escapes the platform workspace")
    return path


def browser_workspace_path(platform_workspace: Path, agent_id: str) -> Path:
    return workspace_member_path(agent_workspace_path(platform_workspace, agent_id), "browser")


def workspace_member_path(workspace: Path, *parts: str) -> Path:
    root = workspace.resolve()
    target = workspace.joinpath(*parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError("Workspace member escapes Agent workspace")
    current = target
    while current != workspace:
        if current.is_symlink():
            raise ValueError("Workspace members cannot be symlinks")
        if current.parent == current:
            raise ValueError("Invalid workspace member")
        current = current.parent
    return target


@dataclass(frozen=True)
class WebDAVPathPolicy:
    """Compile native DeepAgent rules; Context and backend reuse the native matcher."""
    config: AgentWebDAVConfig

    @cached_property
    def permissions(self):
        from deepagents import FilesystemPermission
        from wcmatch import glob
        from pathlib import PurePosixPath
        rules = []
        ancestors = set()
        if self.config.enabled:
            for directory in sorted(self.config.directories, key=lambda d: len(PurePosixPath(d.path).parts), reverse=True):
                path = "/webdav" + directory.path.rstrip("/")
                escaped = glob.escape(path)
                patterns = [escaped, escaped + "/**"]
                rules.append(FilesystemPermission(operations=["read"], paths=patterns))
                rules.append(FilesystemPermission(operations=["write"], paths=patterns,
                    mode="interrupt" if directory.permission == "write" else "deny"))
                ancestors.update(str(p) for p in PurePosixPath(path).parents if str(p).startswith("/webdav"))
        if ancestors:
            rules.append(FilesystemPermission(operations=["read"], paths=[glob.escape(p) for p in sorted(ancestors)]))
        rules.append(FilesystemPermission(operations=["read", "write"], paths=["/webdav", "/webdav/**"], mode="deny"))
        return rules

    def resolve(self, path: str, *, write: bool = False, navigation: bool = False) -> str:
        from pathlib import PurePosixPath
        from deepagents.middleware.filesystem import _check_fs_permission
        if not path.startswith("/") or "\\" in path or "\0" in path or any(p in {".", ".."} for p in path.split("/")):
            raise PermissionError("Invalid WebDAV path")
        path = "/" + "/".join(PurePosixPath(path).parts[1:])
        full = "/webdav" + path.rstrip("/")
        if write and path == "/":
            raise PermissionError("The WebDAV mount root cannot be modified")
        # Exact ancestor permissions allow directory navigation only, never ancestor files.
        selected = any(path == d.path or path.startswith(d.path.rstrip("/") + "/") for d in self.config.directories)
        if (not selected and not navigation) or _check_fs_permission(self.permissions, "write" if write else "read", full) == "deny":
            raise PermissionError("WebDAV permission denied: directory is unselected or read-only")
        return full + ("/" if path == "/" else "")

    def visible_path(self, global_path: str) -> str | None:
        if not global_path.startswith("/webdav/"):
            return None
        try:
            return self.resolve(global_path[len("/webdav"):])
        except (ValueError, PermissionError):
            return None
