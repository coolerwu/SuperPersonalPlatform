from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import sdk_bridge, session_catalog
from app.services.claude_runner import transcript_path
from app.services.settings_store import PlatformSettings

SESSION_ID = "a1b2c3d4-e5f6-4789-abcd-ef0123456789"


class FakeStore:
    def __init__(self, conversations=None):
        if conversations is None:
            conversations = {
                "wx-1": {"session_id": "s1", "channel_id": "ch1", "display_name": "张三"}
            }
        self.raw = {"channel_conversations": conversations}
        self.mutations = 0

    async def get(self):
        return PlatformSettings(
            agents=[],
            channels=[],
            channel_conversations=dict(self.raw.get("channel_conversations") or {}),
        )

    async def mutate_raw(self, fn):
        fn(self.raw)
        self.mutations += 1
        return self.raw


def _session(session_id="s1", summary="hi", last_modified=10, cwd=None):
    return SimpleNamespace(
        session_id=session_id,
        summary=summary,
        last_modified=last_modified,
        first_prompt=summary,
        cwd=str(cwd) if cwd is not None else None,
    )


@pytest.mark.asyncio
async def test_list_sessions_maps_channel_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sdk_bridge,
        "list_sessions",
        lambda directory=None: [
            _session(cwd=tmp_path / "workspaces" / "default"),
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    rows = await catalog.list_sessions("default")
    assert rows[0]["id"] == "s1"
    assert rows[0]["channel_name"] == "张三"
    assert rows[0]["channel_type"] == "wechat"


@pytest.mark.asyncio
async def test_list_sessions_filters_sorts_and_falls_back(tmp_path, monkeypatch):
    workspace = tmp_path / "workspaces" / "default"
    monkeypatch.setattr(
        sdk_bridge,
        "list_sessions",
        lambda directory=None: [
            _session("s1", "first", 10, workspace),
            _session("s2", "", 30, workspace),
            _session("other", "elsewhere", 99, tmp_path / "workspaces" / "other"),
            _session("", "no id", 50, workspace),
            _session("s3", "no cwd", 60, None),
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    rows = await catalog.list_sessions("default")
    assert [row["id"] for row in rows] == ["s2", "s1"]
    assert rows[0]["title"] == "新会话"


@pytest.mark.asyncio
async def test_list_sessions_truncates_long_titles(tmp_path, monkeypatch):
    workspace = tmp_path / "workspaces" / "default"
    monkeypatch.setattr(
        sdk_bridge,
        "list_sessions",
        lambda directory=None: [_session("s1", "标" * 100, 10, workspace)],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    rows = await catalog.list_sessions("default")
    assert rows[0]["title"] == "标" * 80 + "…"


@pytest.mark.asyncio
async def test_messages_extract_text_and_blocks(monkeypatch):
    monkeypatch.setattr(
        sdk_bridge,
        "get_session_messages",
        lambda session_id: [
            SimpleNamespace(
                type="user",
                uuid="u1",
                message={"role": "user", "content": "hi"},
                parent_tool_use_id=None,
            ),
            SimpleNamespace(
                type="assistant",
                uuid="a1",
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hm"},
                        {"type": "text", "text": "hello"},
                    ],
                },
                parent_tool_use_id=None,
            ),
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=Path("/tmp"))
    rows = await catalog.messages("s1")
    assert rows[0] == {"id": "u1", "role": "user", "content": "hi", "blocks": []}
    assert rows[1]["content"] == "hello"
    assert rows[1]["blocks"][0]["type"] == "thinking"


@pytest.mark.asyncio
async def test_messages_skips_tool_results_and_subagents(monkeypatch):
    monkeypatch.setattr(
        sdk_bridge,
        "get_session_messages",
        lambda session_id: [
            SimpleNamespace(
                type="user",
                uuid="u-tool",
                message={
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t", "content": "x"}
                    ],
                },
                parent_tool_use_id=None,
            ),
            SimpleNamespace(
                type="user",
                uuid="u-text",
                message={
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "a"},
                        {"type": "text", "text": "b"},
                    ],
                },
                parent_tool_use_id=None,
            ),
            SimpleNamespace(
                type="assistant",
                uuid="a-sub",
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "sub"}],
                },
                parent_tool_use_id="t1",
            ),
            SimpleNamespace(
                type="assistant",
                uuid="a-ok",
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                },
                parent_tool_use_id=None,
            ),
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=Path("/tmp"))
    rows = await catalog.messages("s1")
    assert [row["id"] for row in rows] == ["u-text", "a-ok"]
    assert rows[0]["content"] == "a\nb"


@pytest.mark.asyncio
async def test_messages_scope_to_agent_rejects_missing_transcript(
    tmp_path, monkeypatch
):
    called = []
    monkeypatch.setattr(
        sdk_bridge,
        "get_session_messages",
        lambda session_id: called.append(session_id) or [],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    assert await catalog.messages("s1", agent_id="default") == []
    assert called == []


@pytest.mark.asyncio
async def test_rename_scope_to_agent(tmp_path, monkeypatch):
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    with pytest.raises(FileNotFoundError):
        await catalog.rename(SESSION_ID, "title", agent_id="default")

    path = transcript_path(SESSION_ID, "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")
    calls = []
    monkeypatch.setattr(
        sdk_bridge,
        "rename_session",
        lambda session_id, title: calls.append((session_id, title)),
    )
    await catalog.rename(SESSION_ID, "title", agent_id="default")
    assert calls == [(SESSION_ID, "title")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_id", "session_id"),
    [
        ("../x", SESSION_ID),
        ("/etc", SESSION_ID),
        ("default", "../../x"),
        ("default", SESSION_ID.upper()),
    ],
)
async def test_delete_rejects_unsafe_ids(
    tmp_path, monkeypatch, agent_id, session_id
):
    calls = []
    monkeypatch.setattr(
        sdk_bridge,
        "delete_session",
        lambda *args, **kwargs: calls.append(args),
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    assert await catalog.delete(agent_id, session_id) is False
    assert calls == []


@pytest.mark.asyncio
async def test_delete_missing_transcript_returns_false(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sdk_bridge,
        "delete_session",
        lambda session_id: calls.append(session_id),
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    assert await catalog.delete("default", SESSION_ID) is False
    assert calls == []


@pytest.mark.asyncio
async def test_delete_tolerates_empty_transcript(tmp_path, monkeypatch):
    path = transcript_path(SESSION_ID, "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("", "utf-8")

    def fake_delete(session_id):
        raise FileNotFoundError(session_id)

    monkeypatch.setattr(sdk_bridge, "delete_session", fake_delete)
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    assert await catalog.delete("default", SESSION_ID) is False


@pytest.mark.asyncio
async def test_delete_removes_transcript(tmp_path, monkeypatch):
    path = transcript_path(SESSION_ID, "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")
    calls = []

    def fake_delete(session_id):
        calls.append(session_id)
        path.unlink()

    monkeypatch.setattr(sdk_bridge, "delete_session", fake_delete)
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    assert await catalog.delete("default", SESSION_ID) is True
    assert calls == [SESSION_ID]
    assert not path.exists()


@pytest.mark.asyncio
async def test_rename_rejects_invalid_uuid_before_sdk(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sdk_bridge,
        "rename_session",
        lambda session_id, title: calls.append((session_id, title)),
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    with pytest.raises(ValueError):
        await catalog.rename("not-a-uuid", "title")
    assert calls == []


@pytest.mark.asyncio
async def test_rename_maps_sdk_value_error(tmp_path, monkeypatch):
    path = transcript_path(SESSION_ID, "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")

    def boom(session_id, title):
        raise ValueError("unknown session")

    monkeypatch.setattr(sdk_bridge, "rename_session", boom)
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    with pytest.raises(ValueError):
        await catalog.rename(SESSION_ID, "title", agent_id="default")


@pytest.mark.asyncio
async def test_messages_without_agent_id_does_not_swallow_sdk_errors(monkeypatch):
    # Callers that pass agent_id are validated before the SDK call; anything
    # else must surface the SDK error instead of silently pretending the
    # session has no messages.
    def boom(session_id):
        raise ValueError("invalid session id")

    monkeypatch.setattr(sdk_bridge, "get_session_messages", boom)
    catalog = session_catalog.SessionCatalog(FakeStore(), home=Path("/tmp"))
    with pytest.raises(ValueError):
        await catalog.messages("not-a-uuid")


@pytest.mark.asyncio
async def test_delete_cascades_screenshots_and_channel_bindings(tmp_path, monkeypatch):
    path = transcript_path(SESSION_ID, "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")
    screenshots = (
        tmp_path / "workspaces" / "default" / "screenshots" / SESSION_ID
    )
    screenshots.mkdir(parents=True)
    (screenshots / "shot.png").write_text("x", "utf-8")
    store = FakeStore(
        {
            "wx-1": {"session_id": SESSION_ID, "channel_id": "ch1", "display_name": "甲"},
            "wx-2": {"session_id": "other", "channel_id": "ch2", "display_name": "乙"},
        }
    )

    def fake_delete(session_id):
        path.unlink()

    monkeypatch.setattr(sdk_bridge, "delete_session", fake_delete)
    catalog = session_catalog.SessionCatalog(store, home=tmp_path)
    assert await catalog.delete("default", SESSION_ID) is True
    assert not screenshots.exists()
    assert list(store.raw["channel_conversations"]) == ["wx-2"]
    assert store.mutations == 1
