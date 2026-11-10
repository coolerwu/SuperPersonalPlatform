from types import SimpleNamespace

import pytest

from app.services import sdk_bridge, session_catalog
from app.services.claude_runner import transcript_path
from app.services.settings_store import PlatformSettings


class FakeStore:
    async def get(self):
        return PlatformSettings(agents=[], channels=[], channel_conversations={
            "wx-1": {"session_id": "s1", "channel_id": "ch1", "display_name": "张三"}
        })


@pytest.mark.asyncio
async def test_list_sessions_maps_channel_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sdk_bridge,
        "list_sessions",
        lambda directory=None: [
            SimpleNamespace(
                session_id="s1",
                summary="hi",
                last_modified=10,
                first_prompt="hi",
                cwd=str(tmp_path / "workspaces" / "default"),
            )
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    rows = await catalog.list_sessions("default")
    assert rows[0]["id"] == "s1"
    assert rows[0]["channel_name"] == "张三"


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
    catalog = session_catalog.SessionCatalog(FakeStore(), home=__import__("pathlib").Path("/tmp"))
    rows = await catalog.messages("s1")
    assert rows[0] == {"id": "u1", "role": "user", "content": "hi", "blocks": []}
    assert rows[1]["content"] == "hello"
    assert rows[1]["blocks"][0]["type"] == "thinking"


@pytest.mark.asyncio
async def test_delete_removes_transcript(tmp_path):
    path = transcript_path("s1", "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    assert await catalog.delete("default", "s1") is True
    assert not path.exists()
