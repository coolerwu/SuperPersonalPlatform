from server.infrastructure.deepagent_runtime import (
    DeepAgentRuntimeOptions,
    RuntimeAttachment,
    RuntimeMessage,
    _runtime_instructions,
    _to_langchain_messages,
    load_agent_files,
    persist_agent_files,
)
from server.domain.agent_config import ModelProvider


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


def test_longterm_memory_prompt_points_memory_requests_to_memories_path() -> None:
    prompt = _runtime_instructions("base prompt", DeepAgentRuntimeOptions(use_longterm_memory=True))

    assert "write_file" in prompt
    assert "/memories/..." in prompt
    assert "Do not use `write_context`" in prompt


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
