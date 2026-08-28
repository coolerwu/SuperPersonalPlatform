import asyncio

from server.domain.agent_config import ModelDefinition, ModelProvider
from server.infrastructure.deepagent_runtime import (
    DeepAgentRuntime,
    DeepAgentRuntimeOptions,
    MEMORY_INDEX_PATH,
    RuntimeAttachment,
    RuntimeMessage,
    _runtime_instructions,
    _to_langchain_messages,
    load_agent_files,
    persist_agent_files,
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
    assert captured["create_kwargs"]["backend"].virtual_mode is True
    assert type(captured["create_kwargs"]["middleware"][0]).__name__ == "TodoListMiddleware"
    assert "store" not in captured["create_kwargs"]
    assert "use_longterm_memory" not in captured["create_kwargs"]
    assert "files" not in captured["input_state"]
    assert (agent_dir / "skills").is_dir()
    assert (agent_dir / "memories").is_dir()
    assert (agent_dir / "memories" / "AGENTS.md").is_file()


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
