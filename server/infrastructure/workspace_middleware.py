"""Give every model request the same workspace contract as the file backend."""
from collections.abc import Awaitable, Callable

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from server.infrastructure.agent_workspace import WORKSPACE_DIRECTORIES
from server.domain.agent_config import AgentWebDAVConfig

WORKSPACE_PROMPT = "\n".join([
    "## Agent Workspace Contract",
    "File tools see '/' as this Agent's private workspace. Use /skills/, /scratch/, etc. directly; do not create another /workspace/ directory.",
    *[f"- /{d.name}/: {d.purpose}" for d in WORKSPACE_DIRECTORIES],
    "/files/ contains local shared knowledge. Use native file tools to find, read and edit it. Temporary notes belong in /scratch/, persistent memory in /memories/, deliverables in /artifacts/.",
    "Do not delete fixed top-level directories. Other Agents and platform configuration are outside this filesystem.",
    "execute_code saves scripts in /scratch/ and deliverables in /artifacts/. Use the returned script_path and file paths.",
    "Inside the code container ONLY, /workspace/input is read-only input, /workspace/work is temporary working space, and /workspace/output receives deliverables. These are temporary mounts, not file-tool paths. They are cleaned after execution; original inputs and saved scripts remain.",
])


class WorkspaceMiddleware(AgentMiddleware):
    def __init__(self, webdav: AgentWebDAVConfig = AgentWebDAVConfig()):
        self.prompt = WORKSPACE_PROMPT
        if webdav.enabled and webdav.directories:
            self.prompt += "\nWebDAV preserves the shared directory hierarchy under /webdav/. More specific child permissions override parents, regardless of configuration order. Unselected directories are inaccessible; ancestors are navigation only."
            for directory in webdav.directories:
                purpose = directory.description or "User documents and shared knowledge from Nutstore."
                self.prompt += f"\n- /webdav{directory.path.rstrip('/')}/: {purpose} Permission: {directory.permission}."
            self.prompt += " All writes to writable WebDAV paths require human approval before execution and update remote Nutstore files only after approval. Read-only paths reject writes. Use /scratch/ for scripts, /artifacts/ for deliverables and /memories/ for private memory."

    def modify_request(self, request: ModelRequest) -> ModelRequest:
        if self.prompt in (request.system_message.text if request.system_message else ""):
            return request
        return request.override(system_message=append_to_system_message(request.system_message, self.prompt))

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(self.modify_request(request))

    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelResponse:
        return await handler(self.modify_request(request))
