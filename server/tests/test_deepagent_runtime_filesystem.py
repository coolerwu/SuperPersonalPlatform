import asyncio

from server.domain.agent_config import ModelDefinition, ModelProvider
from server.domain.run_events import DeepAgentMessageDeltaPayload, DeepAgentSubagentResponsePayload, RunEventType
from server.infrastructure.agent_filesystem_backend import (
    AGENT_WORKSPACE_DIRECTORIES,
    AgentFilesystemBackend,
)
from server.infrastructure.deepagent_runtime import (
    DeepAgentRuntime,
    DeepAgentRuntimeOptions,
    DeepAgentStreamEvent,
    MEMORY_INDEX_PATH,
    RuntimeAttachment,
    RuntimeMessage,
    _runtime_instructions,
    _to_langchain_messages,
    load_agent_files,
    persist_agent_files,
)
from server.infrastructure.skill_improvement_middleware import (
    SKILL_IMPROVEMENT_PROMPT,
    SkillImprovementMiddleware,
)


def test_agent_filesystem_sync_is_limited_to_agent_workspace(tmp_path) -> None:
    agent_dir = tmp_path / "agents" / "assistant"
    (agent_dir / "notes").mkdir(parents=True)
    (agent_dir / "notes" / "profile.md").write_text("hello\nworld", encoding="utf-8")
    (agent_dir / "memory").mkdir()
    (agent_dir / "memory" / "store.json").write_text('{"items": {}}', encoding="utf-8")
    (agent_dir / "binary.bin").write_bytes(b"\xff\x00\xfe")
    (agent_dir / "outside").symlink_to(tmp_path)

    files = load_agent_files(agent_dir)

    assert files["/notes/profile.md"]["content"] == ["hello", "world"]
    assert "/memory/store.json" not in files
    assert "/binary.bin" not in files
    assert not any(path.startswith("/outside") for path in files)

    persist_agent_files(
        agent_dir,
        {
            "/notes/profile.md": {"content": ["updated"]},
            "/artifacts/result.txt": {"content": ["done"]},
            "/memory/store.json": {"content": ["bad"]},
            "/outside/created.txt": {"content": ["bad"]},
            "/../escape.txt": {"content": ["bad"]},
            "relative.txt": {"content": ["bad"]},
        },
    )

    assert (agent_dir / "notes" / "profile.md").read_text(encoding="utf-8") == "updated"
    assert (agent_dir / "artifacts" / "result.txt").read_text(encoding="utf-8") == "done"
    assert (agent_dir / "memory" / "store.json").read_text(encoding="utf-8") == '{"items": {}}'
    assert not (tmp_path / "created.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not (agent_dir / "relative.txt").exists()


def test_runtime_uses_agent_workspace_backend_and_private_skills(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeAgent:
        async def ainvoke(self, input_state, config):
            captured["input_state"] = input_state
            captured["config"] = config
            return {"messages": [type("Message", (), {"content": "ok"})()]}

    def fake_create_deep_agent(**kwargs):
        captured["create_kwargs"] = kwargs
        return FakeAgent()

    import deepagents

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    model = ModelDefinition(
        id="default",
        name="Default",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
    )
    agent_dir = tmp_path / "agents" / "default"
    runtime = DeepAgentRuntime(
        model,
        context_workspace=tmp_path / "context",
        agent_workspace=agent_dir,
    )

    result = asyncio.run(
        runtime.run(
            instructions="base prompt",
            messages=(RuntimeMessage(role="user", content="hello"),),
            options=DeepAgentRuntimeOptions(filesystem_enabled=False),
        )
    )

    assert result == "ok"
    assert captured["create_kwargs"]["skills"] == ["/skills/"]
    assert captured["create_kwargs"]["memory"] == [MEMORY_INDEX_PATH]
    assert captured["create_kwargs"]["backend"].cwd == agent_dir.resolve()
    assert isinstance(captured["create_kwargs"]["backend"], AgentFilesystemBackend)
    assert captured["create_kwargs"]["backend"].virtual_mode is True
    middleware_names = [type(item).__name__ for item in captured["create_kwargs"]["middleware"]]
    assert middleware_names[0] == "TodoListMiddleware"
    assert "SkillImprovementMiddleware" in middleware_names
    assert "store" not in captured["create_kwargs"]
    assert "use_longterm_memory" not in captured["create_kwargs"]
    assert "files" not in captured["input_state"]
    assert all((agent_dir / directory).is_dir() for directory in AGENT_WORKSPACE_DIRECTORIES)
    assert (agent_dir / "memories" / "AGENTS.md").is_file()


def test_agent_filesystem_backend_restricts_mutations_to_managed_directories(tmp_path) -> None:
    backend = AgentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    for directory in AGENT_WORKSPACE_DIRECTORIES:
        (tmp_path / directory).mkdir()

    allowed = backend.write("/artifacts/web-dev/index.html", "ok")
    denied_nested_workspace = backend.write("/workspace/web-dev/index.html", "bad")
    denied_root_file = backend.write("/README.md", "bad")
    denied_traversal = backend.write("/artifacts/../../outside.txt", "bad")

    assert allowed.error is None
    assert (tmp_path / "artifacts" / "web-dev" / "index.html").read_text(encoding="utf-8") == "ok"
    assert "Permission denied" in str(denied_nested_workspace.error)
    assert "virtual '/' is already" in str(denied_nested_workspace.error)
    assert "Writable directories" in str(denied_root_file.error)
    assert "invalid or escapes" in str(denied_traversal.error)
    assert not (tmp_path / "workspace").exists()
    assert not (tmp_path / "README.md").exists()


def test_agent_filesystem_backend_keeps_legacy_top_level_content_read_only(tmp_path) -> None:
    backend = AgentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    legacy_file = tmp_path / "workspace" / "web-dev" / "index.html"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("legacy", encoding="utf-8")

    read_result = backend.read("/workspace/web-dev/index.html")
    edit_result = backend.edit("/workspace/web-dev/index.html", "legacy", "changed")
    delete_result = backend.delete("/workspace/web-dev/index.html")

    assert read_result.error is None
    assert read_result.file_data["content"] == "legacy"
    assert "Permission denied" in str(edit_result.error)
    assert "Permission denied" in str(delete_result.error)
    assert legacy_file.read_text(encoding="utf-8") == "legacy"


def test_agent_filesystem_backend_protects_managed_roots_and_uploaded_files(tmp_path) -> None:
    backend = AgentFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    (tmp_path / "notes").mkdir()

    uploaded = backend.upload_files(
        [
            ("/notes/reference.bin", b"ok"),
            ("/workspace/reference.bin", b"bad"),
        ]
    )
    delete_root = backend.delete("/notes")

    assert uploaded[0].error is None
    assert uploaded[1].error is not None
    assert (tmp_path / "notes" / "reference.bin").read_bytes() == b"ok"
    assert not (tmp_path / "workspace").exists()
    assert "cannot be deleted" in str(delete_root.error)


def test_runtime_adds_skill_improvement_middleware_by_default(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeAgent:
        async def ainvoke(self, input_state, config):
            return {"messages": [type("Message", (), {"content": "ok"})()]}

    def fake_create_deep_agent(**kwargs):
        captured["create_kwargs"] = kwargs
        return FakeAgent()

    import deepagents

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    model = ModelDefinition(
        id="default",
        name="Default",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
    )
    runtime = DeepAgentRuntime(
        model,
        context_workspace=tmp_path / "context",
        agent_workspace=tmp_path / "agents" / "assistant",
    )

    result = asyncio.run(
        runtime.run(
            instructions="base prompt",
            messages=(RuntimeMessage(role="user", content="hello"),),
            options=DeepAgentRuntimeOptions(),
        )
    )

    assert result == "ok"
    middleware_names = [type(item).__name__ for item in captured["create_kwargs"]["middleware"]]
    assert "SkillImprovementMiddleware" in middleware_names


def test_runtime_streams_agent_messages_when_available(tmp_path, monkeypatch) -> None:
    captured = {"events": []}

    class FakeAgent:
        async def astream(self, input_state, config, stream_mode, subgraphs):
            captured["input_state"] = input_state
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            captured["subgraphs"] = subgraphs
            yield ("messages", (type("Chunk", (), {"content": "hel"})(), {"langgraph_node": "model"}))
            yield ("messages", (type("Chunk", (), {"content": "lo"})(), {}))
            yield ("updates", {"agent": {"messages": [type("Message", (), {"content": "hello"})()]}})
            yield ("values", {"messages": [type("Message", (), {"content": "hello final"})()]})

        async def ainvoke(self, input_state, config):
            raise AssertionError("streaming path should not call ainvoke")

    def fake_create_deep_agent(**kwargs):
        captured["create_kwargs"] = kwargs
        return FakeAgent()

    import deepagents

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    model = ModelDefinition(
        id="default",
        name="Default",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
    )
    runtime = DeepAgentRuntime(
        model,
        context_workspace=tmp_path / "context",
        agent_workspace=tmp_path / "agents" / "assistant",
    )

    result = asyncio.run(
        runtime.run(
            instructions="base prompt",
            messages=(RuntimeMessage(role="user", content="hello"),),
            options=DeepAgentRuntimeOptions(),
            stream_callback=lambda event: captured["events"].append(event),
        )
    )

    assert result == "hello final"
    assert captured["stream_mode"] == ["messages", "updates", "values"]
    assert captured["subgraphs"] is True
    assert all(isinstance(event, DeepAgentStreamEvent) for event in captured["events"])
    assert [event.type for event in captured["events"]] == [
        RunEventType.ASSISTANT_DELTA,
        RunEventType.ASSISTANT_DELTA,
        RunEventType.AGENT_UPDATE,
    ]
    assert isinstance(captured["events"][0].payload, DeepAgentMessageDeltaPayload)
    assert captured["events"][0].payload.delta == "hel"
    assert captured["events"][0].payload.node == "model"


def test_runtime_persists_subagent_responses_without_merging_them_into_main_output(tmp_path, monkeypatch) -> None:
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

    captured = {"events": []}

    class FakeAgent:
        async def astream(self, input_state, config, stream_mode, subgraphs):
            assert subgraphs is True
            namespace = ("tools:task-123",)
            yield (
                namespace,
                "messages",
                (AIMessageChunk(content="子任务流"), {"langgraph_node": "model", "lc_agent_name": "researcher"}),
            )
            yield (namespace, "updates", {"model": {"messages": [AIMessage(content="子任务最终响应")]}})
            yield (
                (),
                "updates",
                {"tools": {"messages": [ToolMessage(content="子任务最终响应", name="task", tool_call_id="call-1")]}},
            )
            yield (
                (),
                "updates",
                {"tools": {"messages": [ToolMessage(content="另一个子任务失败", name="task", tool_call_id="call-2")]}},
            )
            yield ((), "messages", (AIMessageChunk(content="主回答"), {"langgraph_node": "model"}))
            yield ((), "values", {"messages": [AIMessage(content="主回答最终内容")]})

        async def ainvoke(self, input_state, config):
            raise AssertionError("streaming path should not call ainvoke")

    def fake_create_deep_agent(**kwargs):
        return FakeAgent()

    import deepagents

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    runtime = DeepAgentRuntime(
        ModelDefinition(
            id="default",
            name="Default",
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o-mini",
        ),
        context_workspace=tmp_path / "context",
        agent_workspace=tmp_path / "agents" / "assistant",
    )

    result = asyncio.run(
        runtime.run(
            instructions="base prompt",
            messages=(RuntimeMessage(role="user", content="hello"),),
            options=DeepAgentRuntimeOptions(),
            stream_callback=lambda event: captured["events"].append(event),
        )
    )

    assert result == "主回答最终内容"
    assert [event.type for event in captured["events"]] == [
        RunEventType.SUBAGENT_RESPONSE,
        RunEventType.AGENT_UPDATE,
        RunEventType.AGENT_UPDATE,
        RunEventType.ASSISTANT_DELTA,
    ]
    payload = captured["events"][0].payload
    assert isinstance(payload, DeepAgentSubagentResponsePayload)
    assert payload.content == "子任务最终响应"
    assert payload.namespace == ("tools:task-123",)
    assert payload.agent == "researcher"
    assert captured["events"][1].payload.preview == ""
    assert captured["events"][2].payload.preview == "另一个子任务失败"


def test_skill_improvement_middleware_wraps_sync_model_call() -> None:
    from langchain_core.messages import SystemMessage

    class FakeRequest:
        system_message = SystemMessage(content="base")

        def override(self, **kwargs):
            request = FakeRequest()
            request.system_message = kwargs["system_message"]
            return request

    captured = {}

    def handler(request):
        captured["request"] = request
        return "sync response"

    response = SkillImprovementMiddleware().wrap_model_call(FakeRequest(), handler)
    system_message = captured["request"].system_message

    assert response == "sync response"
    assert "base" in system_message.text
    assert SKILL_IMPROVEMENT_PROMPT in system_message.text
    assert "Memory is handled separately by MemoryMiddleware" in system_message.text
    assert "/skills/{skill_id}/SKILL.md" in system_message.text
    assert "/improvements/changes/{timestamp}_{change_id}.json" in system_message.text


def test_skill_improvement_middleware_wraps_async_model_call() -> None:
    from langchain_core.messages import SystemMessage

    class FakeRequest:
        system_message = SystemMessage(content="base")

        def override(self, **kwargs):
            request = FakeRequest()
            request.system_message = kwargs["system_message"]
            return request

    captured = {}

    async def handler(request):
        captured["request"] = request
        return "async response"

    response = asyncio.run(SkillImprovementMiddleware().awrap_model_call(FakeRequest(), handler))

    assert response == "async response"
    assert SKILL_IMPROVEMENT_PROMPT in captured["request"].system_message.text


def test_runtime_uses_sqlite_checkpointer_when_thread_id_is_provided(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeAgent:
        async def ainvoke(self, input_state, config):
            await captured["create_kwargs"]["checkpointer"].setup()
            captured["input_state"] = input_state
            captured["config"] = config
            return {"messages": [type("Message", (), {"content": "checkpoint ok"})()]}

    def fake_create_deep_agent(**kwargs):
        captured["create_kwargs"] = kwargs
        return FakeAgent()

    import deepagents

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    model = ModelDefinition(
        id="default",
        name="Default",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
    )
    checkpoint_path = tmp_path / "sessions" / "checkpoints.sqlite"
    runtime = DeepAgentRuntime(
        model,
        context_workspace=tmp_path / "context",
        agent_workspace=tmp_path / "agents" / "assistant",
    )

    result = asyncio.run(
        runtime.run(
            instructions="base prompt",
            messages=(RuntimeMessage(role="user", content="hello"),),
            options=DeepAgentRuntimeOptions(),
            checkpoint_path=checkpoint_path,
            thread_id="session_1",
        )
    )

    assert result == "checkpoint ok"
    assert checkpoint_path.exists()
    assert type(captured["create_kwargs"]["checkpointer"]).__name__ == "AsyncSqliteSaver"
    assert captured["config"]["configurable"]["thread_id"] == "session_1"
    assert captured["config"]["metadata"]["assistant_id"] == "assistant"
    import sqlite3

    conn = sqlite3.connect(checkpoint_path)
    try:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'checkpoints'",
        ).fetchone()
    finally:
        conn.close()


def test_longterm_memory_prompt_points_memory_requests_to_memories_path() -> None:
    prompt = _runtime_instructions("base prompt", DeepAgentRuntimeOptions(use_longterm_memory=True))

    assert "/memories/AGENTS.md" in prompt
    assert "Follow the injected memory guidelines" in prompt
    assert "Do not use `write_context`" in prompt


def test_browser_extract_tool_enables_browser_research_prompt() -> None:
    prompt = _runtime_instructions(
        "base prompt",
        DeepAgentRuntimeOptions(tools=("browser_extract",), use_longterm_memory=False),
    )

    assert "browser_search" in prompt
    assert "browser_extract" in prompt
    assert "current, recent, latest" in prompt


def test_runtime_skips_memory_when_longterm_memory_is_disabled(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeAgent:
        async def ainvoke(self, input_state, config):
            return {"messages": [type("Message", (), {"content": "ok"})()]}

    def fake_create_deep_agent(**kwargs):
        captured["create_kwargs"] = kwargs
        return FakeAgent()

    import deepagents

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    model = ModelDefinition(
        id="default",
        name="Default",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
    )
    agent_dir = tmp_path / "agents" / "default"
    runtime = DeepAgentRuntime(
        model,
        context_workspace=tmp_path / "context",
        agent_workspace=agent_dir,
    )

    asyncio.run(
        runtime.run(
            instructions="base prompt",
            messages=(RuntimeMessage(role="user", content="hello"),),
            options=DeepAgentRuntimeOptions(use_longterm_memory=False),
        )
    )

    assert "memory" not in captured["create_kwargs"]
    assert not (agent_dir / "memories" / "AGENTS.md").exists()


def test_runtime_message_converts_image_attachment_to_openai_content_block(tmp_path) -> None:
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"image-bytes")

    class Human:
        def __init__(self, content):
            self.content = content

    class AI:
        def __init__(self, content):
            self.content = content

    messages = _to_langchain_messages(
        (
            RuntimeMessage(
                role="user",
                content="看图",
                attachments=(RuntimeAttachment(type="image", mime="image/png", path=image_path),),
            ),
        ),
        Human,
        AI,
        ModelProvider.OPENAI_COMPATIBLE,
    )

    assert messages[0].content[0] == {"type": "text", "text": "看图"}
    assert messages[0].content[1]["type"] == "image_url"
    assert messages[0].content[1]["image_url"]["url"].startswith("data:image/png;base64,")
