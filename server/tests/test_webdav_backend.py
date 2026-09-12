import asyncio
import json
from pathlib import Path

import httpx
import pytest
from deepagents.backends import CompositeBackend

from server.app.webdav_context_service import WebDAVContextError, WebDAVContextService
from server.domain.agent_config import AgentConfigError, AgentWebDAVConfig, AgentWebDAVDirectory
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
    config = AgentWebDAVConfig(enabled=True, directories=(AgentWebDAVDirectory(path="/team", permission=permission, description="Team documents"),))
    view = AgentWebDAVView(service, WebDAVPathPolicy(config))
    root = tmp_path / "context/webdav/files"
    (root / "team").mkdir(parents=True)
    (root / "team/note.md").write_text("original")
    (root / "secret.md").write_text("private other mapping")
    local = tmp_path / "agents/a/workspace"
    (local / "scratch").mkdir(parents=True)
    backend = CompositeBackend(default=AgentFilesystemBackend(root_dir=local), routes={"/webdav/": WebDAVFilesystemBackend(view)})
    return backend, view, remote, calls


def test_mapping_native_sync_and_async_write_through(tmp_path):
    backend, view, remote, calls = setup_backend(tmp_path)
    assert "original" in backend.read("/webdav/team/note.md").file_data["content"]
    assert not backend.write("/webdav/team/new.md", "new").error
    assert remote["/dav/notebook/team/new.md"] == b"new"
    assert not asyncio.run(backend.aedit("/webdav/team/note.md", "original", "updated")).error
    assert remote["/dav/notebook/team/note.md"] == b"updated"
    assert (tmp_path / "context/webdav/files/team/note.md").read_text() == "updated"
    assert {d.path for d in view.documents()} == {"/webdav/team/note.md", "/webdav/team/new.md"}
    assert backend.write("/webdav/team/note.md", "duplicate").error
    assert backend.delete("/webdav/").error
    assert backend.upload_files([("/webdav/team/x.png", b"image")])[0].error
    assert not backend.write("/scratch/local.md", "local").error


def test_readonly_and_isolation_cover_context_and_file_tools(tmp_path):
    backend, view, remote, calls = setup_backend(tmp_path, "read")
    assert backend.write("/webdav/team/no.md", "no").error
    assert asyncio.run(backend.aedit("/webdav/team/note.md", "original", "bad")).error
    with pytest.raises(PermissionError):
        asyncio.run(view.write(absolute_path="/webdav/team/no.md", content="no"))
    assert not calls
    assert backend.read("/webdav/../secret.md").error
    assert backend.read("/webdav/secret.md").error
    assert not backend.grep("private", "/").matches
    assert not asyncio.run(backend.agrep("private", "/")).matches
    assert [r["path"] for r in backend.glob("**/*.md", "/webdav/").matches] == ["/webdav/team/note.md"]


def test_symlink_is_invisible_to_all_mapping_readers(tmp_path):
    backend, view, remote, calls = setup_backend(tmp_path)
    root = tmp_path / "context/webdav/files"
    (root / "team/link.md").symlink_to(root / "secret.md")
    assert backend.read("/webdav/team/link.md").error
    assert backend.download_files(["/webdav/team/link.md"])[0].error
    assert not backend.grep("private", "/webdav/").matches
    assert not asyncio.run(backend.agrep("private", "/webdav/")).matches
    assert backend.write("/webdav/team/link.md", "bad").error
    assert not calls


def test_mapping_config_defaults_validation_and_retired_options():
    agent = parse_agent_definition({"id": "a", "name": "A", "system_prompt": "Hi", "deepagent": {"checkpointer": False, "debug": True}})
    assert agent.webdav == AgentWebDAVConfig()
    assert not hasattr(agent.deepagent, "checkpointer")
    for path in ("relative", "/~/private", "/../private", "/team/../private", "/team\\private", "/team/./file"):
        with pytest.raises(AgentConfigError):
            AgentWebDAVDirectory(path=path)
    with pytest.raises(AgentConfigError):
        AgentWebDAVDirectory(permission="invalid")


def test_cache_failure_reports_remote_success(tmp_path, monkeypatch):
    backend, view, remote, calls = setup_backend(tmp_path)
    def fail(**kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(view.service, "_update_cached_write", fail)
    with pytest.raises(WebDAVContextError) as error:
        asyncio.run(view.write(absolute_path="/webdav/team/new.md", content="new", mode="create"))
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
    assert [item.path for item in view.recent_documents(limit=1)] == ["/webdav/team/note.md"]
    assert [item.path for item in view.documents()] == ["/webdav/team/note.md"]
    disabled = AgentWebDAVView(view.service, WebDAVPathPolicy(AgentWebDAVConfig()))
    assert disabled.documents() == []
    with pytest.raises(PermissionError):
        asyncio.run(disabled.write(absolute_path="/webdav/team/note.md", content="no"))


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
        options=DeepAgentRuntimeOptions(name="Agent A", webdav=view.policy.config, tools=())))
    assert isinstance(captured["backend"], CompositeBackend)
    assert captured["name"] == "Agent A"
    assert captured["memory"] == ["/memories/AGENTS.md"]
    assert captured["permissions"] == view.policy.permissions
    assert "permissions" not in captured["subagents"][0]  # native inheritance
    assert captured["interrupt_on"]["write_file"]["allowed_decisions"] == ["approve", "reject"]
    for middleware in (captured["middleware"], captured["subagents"][0]["middleware"]):
        prompt = next(item.prompt for item in middleware if isinstance(item, WorkspaceMiddleware))
        assert "Team documents" in prompt and "Permission: write" in prompt and "remote" in prompt

@pytest.mark.parametrize("parent,child", [("read", "write"), ("write", "read")])
@pytest.mark.parametrize("reverse", [False, True])
def test_nested_permissions_native_backend_and_context(tmp_path, parent, child, reverse):
    from deepagents.middleware.filesystem import FilesystemMiddleware, _check_fs_permission
    from types import SimpleNamespace
    backend, view, remote, calls = setup_backend(tmp_path)
    directories = [AgentWebDAVDirectory("/team", parent), AgentWebDAVDirectory("/team/drafts", child)]
    if reverse:
        directories.reverse()
    view.policy = WebDAVPathPolicy(AgentWebDAVConfig(True, tuple(directories)))
    rules = view.policy.permissions
    assert _check_fs_permission(rules, "write", "/webdav/team/drafts/new.md") == ("interrupt" if child == "write" else "deny")
    assert _check_fs_permission(rules, "write", "/webdav/team/new.md") == ("interrupt" if parent == "write" else "deny")
    assert _check_fs_permission(rules, "read", "/webdav/team2/secret.md") == "deny"
    assert _check_fs_permission(rules, "write", "/scratch/local.md") == "allow"
    middleware = FilesystemMiddleware(backend=backend, _permissions=rules)
    tools = {tool.name: tool for tool in middleware.tools}
    runtime = SimpleNamespace(tool_call_id="test")
    for folder, permission in [("team", parent), ("team/drafts", child)]:
        path = f"/webdav/{folder}/native.md"
        result = tools["write_file"].func(file_path=path, content="native", runtime=runtime)
        assert (result.status == "success") == (permission == "write")
        result = asyncio.run(tools["write_file"].coroutine(file_path=path.replace("native", "async"), content="async", runtime=runtime))
        assert (result.status == "success") == (permission == "write")
        if permission == "write":
            asyncio.run(view.write(absolute_path=path.replace("native", "context"), content="context", mode="create"))
        else:
            with pytest.raises(PermissionError):
                asyncio.run(view.write(absolute_path=path.replace("native", "context"), content="context"))
        assert bool(backend.write(path.replace("native", "direct"), "direct").error) == (permission == "read")
    listing = tools["ls"].func(runtime=runtime, path="/webdav/")
    assert "team" in listing.content and "secret" not in listing.content


def test_ancestor_navigation_and_literal_glob_names(tmp_path):
    from deepagents.middleware.filesystem import FilesystemMiddleware, _check_fs_permission
    from types import SimpleNamespace
    backend, view, _, _ = setup_backend(tmp_path)
    root = view.service._files_dir
    (root / "team/[drafts]").mkdir()
    (root / "team/[drafts]/note.md").write_text("visible")
    view.policy = WebDAVPathPolicy(AgentWebDAVConfig(True, (AgentWebDAVDirectory("/team/[drafts]", "read"),)))
    tools = {t.name: t for t in FilesystemMiddleware(backend=backend, _permissions=view.policy.permissions).tools}
    runtime = SimpleNamespace(tool_call_id="test")
    assert "team" in tools["ls"].func(runtime=runtime, path="/webdav/").content
    listing = tools["ls"].func(runtime=runtime, path="/webdav/team").content
    assert "[drafts]" in listing and "note.md" not in listing
    assert backend.read("/webdav/team/note.md").error
    assert [r["path"] for r in backend.glob("**/*.md", "/webdav/").matches] == ["/webdav/team/[drafts]/note.md"]
    assert _check_fs_permission(view.policy.permissions, "read", "/webdav/team/d/note.md") == "deny"
    # An ancestor replaced by a file must never acquire read permission.
    (root / "team/[drafts]/note.md").unlink()
    (root / "team/[drafts]").rmdir()
    (root / "team/note.md").unlink()
    (root / "team").rmdir()
    (root / "team").write_text("private ancestor file")
    assert backend.read("/webdav/team").error
    assert backend.download_files(["/webdav/team"])[0].error


def test_multiple_directory_validation_and_empty_permissions():
    from server.infrastructure.config import parse_agent_webdav
    from deepagents.middleware.filesystem import _check_fs_permission
    for raw in [{"directories": {}}, {"directories": [None]}, {"path": "/"},
                {"directories": [{"path": "/team/"}, {"path": "//team"}]}]:
        with pytest.raises((AgentConfigError, ValueError)):
            parse_agent_webdav(raw)
    config = parse_agent_webdav({"enabled": True, "directories": [{"path": "/笔记"}, {"path": "/资料", "permission": "read"}]})
    assert config.directories[0].permission == "write"
    policy = WebDAVPathPolicy(config)
    assert policy.resolve("/笔记/new.md", write=True) == "/webdav/笔记/new.md"
    with pytest.raises(PermissionError):
        policy.resolve("/笔记2/new.md")
    for config in (AgentWebDAVConfig(True), AgentWebDAVConfig(False, config.directories)):
        assert _check_fs_permission(WebDAVPathPolicy(config).permissions, "read", "/webdav/笔记/new.md") == "deny"

@pytest.mark.parametrize('delegate', [False, True])
@pytest.mark.parametrize('decision', ['approve', 'reject'])
@pytest.mark.parametrize('operation', ['write_file', 'edit_file'])
def test_real_graph_webdav_hitl(tmp_path, delegate, decision, operation):
    from deepagents import create_deep_agent
    from deepagents.middleware._fs_interrupt import _build_interrupt_on_from_permissions
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatResult, ChatGeneration
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    backend, view, remote, calls = setup_backend(tmp_path)
    class Model(BaseChatModel):
        @property
        def _llm_type(self): return 'hitl-test'
        def bind_tools(self, tools, **kwargs): return self
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            is_child = any('CHILD' in m.text for m in messages if m.type == 'system')
            if any(isinstance(m, ToolMessage) for m in messages):
                result = AIMessage(content='done')
            elif delegate and not is_child:
                result = AIMessage(content='', tool_calls=[{'name':'task','id':'child','args':{'description':'Write the document','subagent_type':'general-purpose'}}])
            else:
                args = {'file_path':'/webdav/team/hitl.md','content':'approved'} if operation == 'write_file' else {'file_path':'/webdav/team/note.md','old_string':'original','new_string':'approved'}
                result = AIMessage(content='', tool_calls=[{'name':operation,'id':'write','args':args}])
            return ChatResult(generations=[ChatGeneration(message=result)])
    permissions=view.policy.permissions
    interrupt_on=_build_interrupt_on_from_permissions(permissions)
    for rule in interrupt_on.values(): rule['allowed_decisions']=['approve','reject']
    saver=InMemorySaver()
    def build():
        return create_deep_agent(model=Model(),backend=backend,permissions=permissions,interrupt_on=interrupt_on,checkpointer=saver,
            subagents=[{'name':'general-purpose','description':'child','system_prompt':'CHILD'}])
    config={'configurable':{'thread_id':'approval'}}
    graph=build()
    first=asyncio.run(graph.ainvoke({'messages':[{'role':'user','content':'Write'}]},config))
    assert first.get('__interrupt__')
    assert calls == []
    assert remote == {'/dav/notebook/team/note.md':b'original'}
    # Rebuild the graph to exercise checkpoint-based approval resumption.
    resumed=asyncio.run(build().ainvoke(Command(resume={'decisions':[{'type':decision}]}),config))
    assert not resumed.get('__interrupt__')
    if decision == 'approve':
        target='hitl.md' if operation=='write_file' else 'note.md'
        assert remote['/dav/notebook/team/'+target]==b'approved'
        assert (view.service._files_dir/'team'/target).read_text()=='approved'
        assert sum(method=='PUT' for method,_ in calls)==1
    else:
        assert calls==[]
        assert not (view.service._files_dir/'team/hitl.md').exists()
        assert (view.service._files_dir/'team/note.md').read_text()=='original'


def test_real_graph_webdav_hitl_resumes_once_with_sqlite_checkpoint(tmp_path):
    from deepagents import create_deep_agent
    from deepagents.middleware._fs_interrupt import _build_interrupt_on_from_permissions
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command

    backend, view, remote, calls = setup_backend(tmp_path)

    class Model(BaseChatModel):
        model_calls: int = 0

        @property
        def _llm_type(self):
            return "sqlite-hitl-test"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.model_calls += 1
            if any(isinstance(message, ToolMessage) for message in messages):
                message = AIMessage(content="done")
            else:
                message = AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "write_file",
                        "id": "write-once",
                        "args": {"file_path": "/webdav/team/once.md", "content": "approved"},
                    }],
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    async def scenario():
        permissions = view.policy.permissions
        interrupt_on = _build_interrupt_on_from_permissions(permissions)
        for rule in interrupt_on.values():
            rule["allowed_decisions"] = ["approve", "reject"]
        model = Model()
        checkpoint_path = tmp_path / "sessions" / "checkpoints.sqlite"
        checkpoint_path.parent.mkdir(parents=True)
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            def build():
                return create_deep_agent(
                    model=model,
                    backend=backend,
                    permissions=permissions,
                    interrupt_on=interrupt_on,
                    checkpointer=saver,
                )

            config = {"configurable": {"thread_id": "single-session"}}
            first = await build().ainvoke({"messages": [{"role": "user", "content": "Write"}]}, config)
            interrupt = first["__interrupt__"][0]
            resumed = await build().ainvoke(
                Command(resume={interrupt.id: {"decisions": [{"type": "approve"}]}}),
                config,
            )
            return resumed, model.model_calls

    resumed, model_calls = asyncio.run(scenario())

    assert not resumed.get("__interrupt__")
    assert remote["/dav/notebook/team/once.md"] == b"approved"
    assert (view.service._files_dir / "team" / "once.md").read_text() == "approved"
    assert sum(method == "PUT" for method, _ in calls) == 1
    assert model_calls == 2


def test_shared_files_and_removed_notes(tmp_path):
    from server.infrastructure.shared_files_backend import SharedFilesBackend
    root=tmp_path/'knowledge'
    root.mkdir()
    backend=SharedFilesBackend(root_dir=root,virtual_mode=True)
    assert not backend.write('/existing.md','shared').error
    assert not asyncio.run(backend.aedit('/existing.md','shared','updated')).error
    assert backend.read('/existing.md').file_data['content']=='updated'
    assert backend.write('/binary.png','no').error
    assert backend.delete('/existing.md').error
    (root/'alias.md').symlink_to(tmp_path/'private.md')
    assert backend.read('/alias.md').error
    assert backend.write('/alias.md','no').error
    private=AgentFilesystemBackend(root_dir=tmp_path,virtual_mode=True)
    (tmp_path/'notes').mkdir()
    (tmp_path/'notes/old.md').write_text('retired')
    assert private.read('/notes/old.md').error
    assert private.write('/notes/new.md','no').error
    assert not private.grep('retired','/').matches
