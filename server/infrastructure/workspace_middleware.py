"""Give every model request the same workspace contract as the file backend."""
from collections.abc import Awaitable, Callable

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from server.infrastructure.agent_workspace import WORKSPACE_DIRECTORIES

WORKSPACE_PROMPT = "\n".join([
    "## Agent Workspace Contract",
    "File tools see '/' as this Agent's private workspace. Use /skills/, /scratch/, etc. directly; do not create another /workspace/ directory.",
    *[f"- /{d.name}/: {d.purpose}" for d in WORKSPACE_DIRECTORIES],
    "Do not delete fixed top-level directories. Other Agents and platform configuration are outside this filesystem.",
    "execute_code saves scripts in /scratch/ and deliverables in /artifacts/. Use the returned script_path and file paths.",
    "Inside the code container ONLY, /workspace/input is read-only input, /workspace/work is temporary working space, and /workspace/output receives deliverables. These are temporary mounts, not file-tool paths. They are cleaned after execution; original inputs and saved scripts remain.",
])


class WorkspaceMiddleware(AgentMiddleware):
    def modify_request(self, request: ModelRequest) -> ModelRequest:
        if WORKSPACE_PROMPT in (request.system_message.text if request.system_message else ""):
            return request
        return request.override(system_message=append_to_system_message(request.system_message, WORKSPACE_PROMPT))

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(self.modify_request(request))

    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelResponse:
        return await handler(self.modify_request(request))
