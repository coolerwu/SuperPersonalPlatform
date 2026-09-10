"""Canonical Agent workspace paths and directory policy."""
from dataclasses import dataclass
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
    WorkspaceDirectory("notes", "Your working notes; shared user knowledge belongs in Context tools."),
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
    """One path authority for both native file and Context tools."""
    config: "AgentWebDAVConfig"

    def resolve(self, path: str, *, write: bool = False) -> str:
        from pathlib import PurePosixPath
        if not self.config.enabled or (write and self.config.permission != "write"):
            raise PermissionError("WebDAV mapping is disabled or read-only")
        if not path.startswith("/") or "\\" in path or "\0" in path or any(p in {".", ".."} for p in path.split("/")):
            raise PermissionError("Invalid WebDAV path")
        if write and str(PurePosixPath(path)) == "/":
            raise PermissionError("The WebDAV mount root cannot be modified")
        return "/webdav/" + "/".join(p for p in (self.config.path.strip("/"), path.strip("/")) if p)

    def visible_path(self, global_path: str) -> str | None:
        from pathlib import PurePosixPath
        if not self.config.enabled or not global_path.startswith("/webdav/"):
            return None
        try:
            relative = PurePosixPath(global_path).relative_to(PurePosixPath("/webdav") / self.config.path.lstrip("/"))
            self.resolve("/" + relative.as_posix())
        except (ValueError, PermissionError):
            return None
        return "/webdav/" + relative.as_posix()
