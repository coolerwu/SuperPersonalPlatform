import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import SystemMessage

from server.infrastructure.agent_workspace import agent_workspace_path, browser_workspace_path, WORKSPACE_DIRECTORIES
from server.infrastructure.agent_filesystem_backend import AgentFilesystemBackend
from server.infrastructure.workspace_middleware import WorkspaceMiddleware, WORKSPACE_PROMPT


def test_workspace_identity_and_browser_share_root(tmp_path):
    from server.app.browser_profile_service import BrowserProfileService
    from server.app.run_service import RunService
    for agent in ("first", "second"):
        expected = tmp_path / "agents" / agent / "workspace"
        assert RunService(tmp_path)._agent_workspace(agent) == expected
        assert BrowserProfileService(tmp_path).profile_dir(agent) == expected / "browser"
        assert browser_workspace_path(tmp_path, agent) == expected / "browser"
    with pytest.raises(ValueError):
        agent_workspace_path(tmp_path, "../first")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_browser_secret_unavailable_to_all_file_operations(tmp_path, asynchronous):
    root = agent_workspace_path(tmp_path, "first")
    (root / "browser").mkdir(parents=True)
    (root / "notes").mkdir()
    (root / "browser" / "Cookies.txt").write_text("SECRET_NEVER_READ")
    (root / "notes" / "public.txt").write_text("public marker")
    (root / "notes" / "alias.txt").symlink_to(root / "browser" / "Cookies.txt")
    (root / "notes" / "aliasdir").symlink_to(root / "browser", target_is_directory=True)
    backend = AgentFilesystemBackend(root_dir=root, virtual_mode=True)
    def call(name, *args):
        return asyncio.run(getattr(backend, 'a' + name)(*args)) if asynchronous else getattr(backend, name)(*args)
    for path in ("/browser/Cookies.txt", "/notes/alias.txt", "/notes/aliasdir/Cookies.txt", "/notes/../browser/Cookies.txt"):
        assert call("read", path).error
        assert call("download_files", [path])[0].error
        assert call("write", path, "replace").error
        assert call("edit", path, "SECRET", "bad").error
        assert call("delete", path).error
        assert call("upload_files", [(path, b"bad")])[0].error
    assert call("ls", "/browser").error
    assert all("browser" not in x["path"] for x in call("ls", "/").entries)
    assert not call("grep", "SECRET_NEVER_READ", "/").matches
    assert not call("grep", "SECRET_NEVER_READ", "/browser").matches
    assert all("Cookies" not in x["path"] and "alias" not in x["path"] for x in call("glob", "**/*", "/").matches)
    assert call("grep", "public marker", "/").matches[0]["path"] == "/notes/public.txt"
    assert (root / "browser" / "Cookies.txt").read_text() == "SECRET_NEVER_READ"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_workspace_prompt_is_per_request_and_idempotent(asynchronous):
    class Request:
        def __init__(self, system_message):
            self.system_message = system_message
        def override(self, **kwargs):
            return Request(**kwargs)
    middleware = WorkspaceMiddleware()
    original = Request(SystemMessage(content="personality"))
    async def handler(request):
        return request
    if asynchronous:
        request = asyncio.run(middleware.awrap_model_call(original, handler))
        request = asyncio.run(middleware.awrap_model_call(request, handler))
    else:
        request = middleware.wrap_model_call(original, lambda r: r)
        request = middleware.wrap_model_call(request, lambda r: r)
    assert request.system_message.text.count(WORKSPACE_PROMPT) == 1
    assert original.system_message.text == "personality"
    for directory in WORKSPACE_DIRECTORIES:
        assert f"/{directory.name}/" in request.system_message.text


def test_real_main_and_subagent_receive_workspace_prompt(tmp_path):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatResult, ChatGeneration
    from deepagents import create_deep_agent
    seen = []
    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "workspace-test"
        def bind_tools(self, tools, **kwargs):
            return self
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append(messages)
            if len(seen) == 1:
                answer = AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "Return child done", "subagent_type": "general-purpose"}, "id": "child-call"}])
            else:
                answer = AIMessage(content="done")
            return ChatResult(generations=[ChatGeneration(message=answer)])
    graph = create_deep_agent(
        model=Model(), backend=AgentFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        middleware=[WorkspaceMiddleware()],
        subagents=[{"name": "general-purpose", "description": "child", "system_prompt": "child instructions", "middleware": [WorkspaceMiddleware()]}],
    )
    asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": "Delegate to child"}]}))
    assert len(seen) >= 3
    for messages in seen:
        text = "\n".join(m.text for m in messages if m.type == "system")
        assert text.count(WORKSPACE_PROMPT) == 1
