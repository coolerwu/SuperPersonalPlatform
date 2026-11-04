import json

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import MemoryItem
from app.services import memory_service


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, messages):
        return _FakeMessage(self._content)


async def _cleanup(content: str) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(MemoryItem).where(MemoryItem.content == content))
        await db.commit()


@pytest.mark.asyncio
async def test_extraction_writes_user_and_agent_scopes(client, monkeypatch) -> None:
    user_content = "自动记忆测试：用户偏好先给结论"
    agent_content = "自动记忆测试：当前 Agent 用中文回答"
    payload = json.dumps(
        {
            "memories": [
                {"action": "add", "scope": "user", "content": user_content},
                {"action": "add", "scope": "agent", "content": agent_content},
            ]
        }
    )
    model = _FakeModel(payload)

    async def no_candidates(**kwargs):
        return []

    monkeypatch.setattr(memory_service, "find_merge_candidates", no_candidates)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", lambda **kwargs: model)
    provider = type(
        "Provider", (), {"api_key": "test", "base_url": "http://x", "model": "m"}
    )()

    try:
        changes = await memory_service.extract_memories(
            provider=provider,
            agent_id="default",
            user_content="以后回答先给结论，Agent 用中文回答",
            assistant_output="好的",
            run_id="run-memory-scope-test",
        )

        by_scope = {change.scope: change for change in changes}
        assert set(by_scope) == {"user", "agent"}
        assert by_scope["user"].action == "add"
        assert by_scope["user"].content == user_content
        assert by_scope["agent"].action == "add"
        assert by_scope["agent"].content == agent_content

        async with SessionLocal() as db:
            user = await db.scalar(
                select(MemoryItem).where(MemoryItem.content == user_content)
            )
            agent = await db.scalar(
                select(MemoryItem).where(MemoryItem.content == agent_content)
            )
        assert user is not None
        assert user.scope == "user"
        assert user.agent_id is None
        assert agent is not None
        assert agent.scope == "agent"
        assert agent.agent_id == "default"
    finally:
        await _cleanup(user_content)
        await _cleanup(agent_content)


@pytest.mark.asyncio
async def test_extraction_update_keeps_existing_scope(client, monkeypatch) -> None:
    content = "自动记忆测试：用户偏好简短回答"
    item = await memory_service.create_memory(scope="user", content=content)
    assert item is not None

    payload = json.dumps(
        {
            "memories": [
                {
                    "action": "update",
                    "scope": "agent",
                    "id": item.id,
                    "content": "自动记忆测试：用户偏好非常简短的回答",
                }
            ]
        }
    )
    model = _FakeModel(payload)

    async def candidates(**kwargs):
        return [item]

    monkeypatch.setattr(memory_service, "find_merge_candidates", candidates)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", lambda **kwargs: model)
    provider = type(
        "Provider", (), {"api_key": "test", "base_url": "http://x", "model": "m"}
    )()

    try:
        changes = await memory_service.extract_memories(
            provider=provider,
            agent_id="default",
            user_content="回答再短一点",
            assistant_output="好的",
            run_id="run-memory-scope-update",
        )

        assert len(changes) == 1
        assert changes[0].action == "update"
        assert changes[0].scope == "user"

        async with SessionLocal() as db:
            stored = await db.get(MemoryItem, item.id)
        assert stored is not None
        assert stored.scope == "user"
        assert stored.content == "自动记忆测试：用户偏好非常简短的回答"
    finally:
        await memory_service.delete_memory(item.id)


@pytest.mark.asyncio
async def test_merge_candidates_include_user_memories(client) -> None:
    content = "自动记忆测试：用户希望回答保持简短"
    item = await memory_service.create_memory(scope="user", content=content)
    assert item is not None
    try:
        candidates = await memory_service.find_merge_candidates(
            agent_id="default", query="回答保持简短", limit=12
        )
        assert any(candidate.id == item.id for candidate in candidates)
    finally:
        await memory_service.delete_memory(item.id)
