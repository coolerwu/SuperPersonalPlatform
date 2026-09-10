import asyncio
import json
from pathlib import Path

import httpx
import pytest
from deepagents.backends import CompositeBackend

from server.app.webdav_context_service import WebDAVContextError, WebDAVContextService
from server.domain.agent_config import AgentConfigError, AgentWebDAVConfig
from server.infrastructure.agent_filesystem_backend import AgentFilesystemBackend
from server.infrastructure.agent_workspace import WebDAVPathPolicy
from server.infrastructure.config import ContextConfig, NutstoreConfig, WebDAVSyncConfig, parse_agent_definition
from server.infrastructure.nutstore_webdav import NutstoreWebDAVClient
from server.infrastructure.webdav_backend import AgentWebDAVView, WebDAVFilesystemBackend


def setup_backend(tmp_path, permission="write"):
    remote = {"/dav/notebook/team/note.md": b"original"}
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "GET":
            return httpx.Response(200, content=remote[path]) if path in remote else httpx.Response(404)
        if request.method == "PUT":
            remote[path] = request.content
            return httpx.Response(201)
        return httpx.Response(500)
    nutstore = NutstoreConfig(enabled=True, username="u", password="p")
    service = WebDAVContextService(workspace=tmp_path, nutstore=nutstore,
        context=ContextConfig(webdav_sync=WebDAVSyncConfig(enabled=True, root_path="/notebook")),
        client=NutstoreWebDAVClient(nutstore, transport=httpx.MockTransport(handle)))
    config = AgentWebDAVConfig(enabled=True, path="/team", permission=permission, description="Team documents")
    view = AgentWebDAVView(service, WebDAVPathPolicy(config))
    root = tmp_path / "context/webdav/files"
    (root / "team").mkdir(parents=True)
    (root / "team/note.md").write_text("original")
    (root / "secret.md").write_text("private other mapping")
    local = tmp_path / "agents/a/workspace"
    (local / "notes").mkdir(parents=True)
    backend = CompositeBackend(default=AgentFilesystemBackend(root_dir=local), routes={"/webdav/": WebDAVFilesystemBackend(view)})
    return backend, view, remote, calls


def test_mapping_native_sync_and_async_write_through(tmp_path):
    backend, view, remote, calls = setup_backend(tmp_path)
    assert "original" in backend.read("/webdav/note.md").file_data["content"]
    assert not backend.write("/webdav/new.md", "new").error
    assert remote["/dav/notebook/team/new.md"] == b"new"
    assert not asyncio.run(backend.aedit("/webdav/note.md", "original", "updated")).error
    assert remote["/dav/notebook/team/note.md"] == b"updated"
    assert (tmp_path / "context/webdav/files/team/note.md").read_text() == "updated"
    assert {d.path for d in view.documents()} == {"/webdav/note.md", "/webdav/new.md"}
    assert backend.write("/webdav/note.md", "duplicate").error
    assert backend.delete("/webdav/").error
    assert backend.upload_files([("/webdav/x.png", b"image")])[0].error
    assert not backend.write("/notes/local.md", "local").error


def test_readonly_and_isolation_cover_context_and_file_tools(tmp_path):
    backend, view, remote, calls = setup_backend(tmp_path, "read")
    assert backend.write("/webdav/no.md", "no").error
    assert asyncio.run(backend.aedit("/webdav/note.md", "original", "bad")).error
    with pytest.raises(PermissionError):
        asyncio.run(view.write(absolute_path="/webdav/no.md", content="no"))
    assert not calls
    assert backend.read("/webdav/../secret.md").error
    assert backend.read("/webdav/secret.md").error
    assert not backend.grep("private", "/").matches
    assert not asyncio.run(backend.agrep("private", "/")).matches
    assert [r["path"] for r in backend.glob("**/*.md", "/webdav/").matches] == ["/webdav/note.md"]


def test_symlink_is_invisible_to_all_mapping_readers(tmp_path):
    backend, view, remote, calls = setup_backend(tmp_path)
    root = tmp_path / "context/webdav/files"
    (root / "team/link.md").symlink_to(root / "secret.md")
    assert backend.read("/webdav/link.md").error
    assert backend.download_files(["/webdav/link.md"])[0].error
    assert not backend.grep("private", "/webdav/").matches
    assert not asyncio.run(backend.agrep("private", "/webdav/")).matches
    assert backend.write("/webdav/link.md", "bad").error
    assert not calls


def test_mapping_config_defaults_validation_and_retired_options():
    agent = parse_agent_definition({"id": "a", "name": "A", "system_prompt": "Hi", "deepagent": {"checkpointer": False, "debug": True}})
    assert agent.webdav == AgentWebDAVConfig()
    assert not hasattr(agent.deepagent, "checkpointer")
    for path in ("relative", "/../private", "/team/../private", "/team\\private", "/team/./file"):
        with pytest.raises(AgentConfigError):
            AgentWebDAVConfig(enabled=True, path=path)
    with pytest.raises(AgentConfigError):
        AgentWebDAVConfig(permission="invalid")


def test_cache_failure_reports_remote_success(tmp_path, monkeypatch):
    backend, view, remote, calls = setup_backend(tmp_path)
    def fail(**kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(view.service, "_update_cached_write", fail)
    with pytest.raises(WebDAVContextError) as error:
        asyncio.run(view.write(absolute_path="/webdav/new.md", content="new", mode="create"))
    assert error.value.diagnostics["remote_written"] is True
    assert remote["/dav/notebook/team/new.md"] == b"new"


def test_separate_service_instances_serialize_transactions(tmp_path):
    backend, view, remote, calls = setup_backend(tmp_path)
    second = WebDAVContextService(workspace=tmp_path, nutstore=view.service._nutstore, context=view.service._context, client=view.service._client)
    async def execute():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def hold():
            async with view.service.transaction():
                entered.set()
                await release.wait()
        holder = asyncio.create_task(hold())
        await entered.wait()
        writer = asyncio.create_task(second.write(absolute_path="/webdav/team/new.md", content="new", mode="create"))
        await asyncio.sleep(0.1)
        assert not calls
        release.set()
        await asyncio.gather(holder, writer)
    asyncio.run(execute())
    assert remote["/dav/notebook/team/new.md"] == b"new"


def test_recent_documents_filter_before_limit_and_disabled_view(tmp_path):
    backend, view, remote, calls = setup_backend(tmp_path)
    files = {}
    for i in range(12):
        path = tmp_path / f"context/webdav/files/other/{i}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("other agent")
        files[f"/webdav/other/{i}.md"] = {"cache_path": f"files/other/{i}.md", "modified": "2026-09-10T00:00:00+00:00"}
    files["/webdav/team/note.md"] = {"cache_path": "files/team/note.md", "modified": "2026-09-09T00:00:00+00:00"}
    (tmp_path / "context/webdav/index.json").write_text(json.dumps({"files": files}))
    assert [item.path for item in view.recent_documents(limit=1)] == ["/webdav/note.md"]
    assert [item.path for item in view.documents()] == ["/webdav/note.md"]
    disabled = AgentWebDAVView(view.service, WebDAVPathPolicy(AgentWebDAVConfig()))
    assert disabled.documents() == []
    with pytest.raises(PermissionError):
        asyncio.run(disabled.write(absolute_path="/webdav/note.md", content="no"))


def test_runtime_routes_backend_and_passes_description_to_both_agents(tmp_path, monkeypatch):
    from server.domain.agent_config import ModelDefinition
    from server.infrastructure.deepagent_runtime import DeepAgentRuntime, DeepAgentRuntimeOptions, RuntimeMessage
    from server.infrastructure.workspace_middleware import WorkspaceMiddleware
    import deepagents
    backend, view, remote, calls = setup_backend(tmp_path)
    captured = {}
    class Agent:
        async def ainvoke(self, state, config):
            return {"messages": [type("Message", (), {"content": "ok"})()]}
    def create(**kwargs):
        captured.update(kwargs)
        return Agent()
    monkeypatch.setattr(deepagents, "create_deep_agent", create)
    monkeypatch.setattr("server.infrastructure.deepagent_runtime._webdav_context_service", lambda *args: view)
    runtime = DeepAgentRuntime(ModelDefinition(id="m", name="M", base_url="https://example.com", api_key="test", model="m"),
        context_workspace=tmp_path / "context", agent_workspace=tmp_path / "agents/a/workspace", agent_id="a")
    asyncio.run(runtime.run(instructions="Hi", messages=(RuntimeMessage(role="user", content="Hi"),),
        options=DeepAgentRuntimeOptions(name="Agent A", webdav=view.policy.config, tools=("write_context",))))
    assert isinstance(captured["backend"], CompositeBackend)
    assert captured["name"] == "Agent A"
    assert captured["memory"] == ["/memories/AGENTS.md"]
    assert captured["interrupt_on"] == {"write_context": {"allowed_decisions": ["approve", "reject"]}}
    for middleware in (captured["middleware"], captured["subagents"][0]["middleware"]):
        prompt = next(item.prompt for item in middleware if isinstance(item, WorkspaceMiddleware))
        assert "Team documents" in prompt and "Permission: write" in prompt and "remote" in prompt
