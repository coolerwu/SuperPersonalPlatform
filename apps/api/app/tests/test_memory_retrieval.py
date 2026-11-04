from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import Agent, MemoryItem, Provider, RunMemory
from app.services import memory_service


@pytest.fixture(autouse=True)
async def _database():
    await init_db()
    async with SessionLocal() as db:
        await db.execute(delete(RunMemory))
        await db.execute(delete(MemoryItem))
        await db.commit()


async def _ensure_provider() -> None:
    async with SessionLocal() as db:
        if await db.get(Provider, "default") is None:
            db.add(Provider(id="default", name="default", base_url="", api_key="", model=""))
            await db.commit()


async def _create_agent() -> str:
    await _ensure_provider()
    agent_id = f"memory-agent-{uuid.uuid4().hex[:12]}"
    async with SessionLocal() as db:
        db.add(
            Agent(
                id=agent_id,
                name=agent_id,
                instructions="",
                provider_id="default",
            )
        )
        await db.commit()
    return agent_id


async def _add_memory(
    *,
    scope: str,
    content: str,
    agent_id: str | None = None,
    updated_at: datetime | None = None,
) -> str:
    item = await memory_service.create_memory(
        scope=scope, content=content, agent_id=agent_id
    )
    assert item is not None
    if updated_at is not None:
        async with SessionLocal() as db:
            stored = await db.get(MemoryItem, item.id)
            assert stored is not None
            stored.updated_at = updated_at
            await db.commit()
    return item.id


@pytest.mark.asyncio
async def test_only_relevant_memory_is_injected() -> None:
    agent = await _create_agent()
    relevant = await _add_memory(scope="user", content="用户喜欢简短回答")
    unrelated = await _add_memory(scope="user", content="用户住在杭州")
    await _add_memory(
        scope="agent", agent_id=agent, content="Agent 使用 systemd 部署"
    )

    block, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="以后回答简短一点"
    )

    assert ids == [relevant]
    assert "简短回答" in block
    assert "杭州" not in block
    async with SessionLocal() as db:
        stored = await db.get(MemoryItem, unrelated)
        assert stored is not None
        assert stored.last_used_at is None


@pytest.mark.asyncio
async def test_no_relevant_memory_returns_empty_block() -> None:
    agent = await _create_agent()
    await _add_memory(scope="user", content="部署使用 systemd 单进程")

    block, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="今天天气怎么样"
    )

    assert block == ""
    assert ids == []


@pytest.mark.asyncio
async def test_user_memory_is_not_fully_injected() -> None:
    agent = await _create_agent()
    relevant = await _add_memory(scope="user", content="项目部署使用 systemd")
    await _add_memory(scope="user", content="用户喜欢喝咖啡")
    await _add_memory(scope="user", content="用户住在杭州")
    await _add_memory(scope="user", content="用户的猫叫咪咪")
    await _add_memory(scope="user", content="用户周末去爬山")

    _, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="部署方案是什么"
    )

    assert ids == [relevant]


@pytest.mark.asyncio
async def test_duplicate_content_prefers_user_scope() -> None:
    agent = await _create_agent()
    user_id = await _add_memory(scope="user", content="用户喜欢简短回答")
    await _add_memory(scope="agent", agent_id=agent, content="用户喜欢简短回答")

    block, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="简短回答"
    )

    assert ids == [user_id]
    assert "- [用户] 用户喜欢简短回答" in block
    assert block.count("用户喜欢简短回答") == 1


@pytest.mark.asyncio
async def test_long_memory_is_clipped() -> None:
    agent = await _create_agent()
    long_content = "回答" + "很" * 400
    await _add_memory(scope="user", content=long_content)

    block, _ = await memory_service.retrieve_for_prompt(agent_id=agent, query="回答")

    line = next(line for line in block.splitlines() if line.startswith("- [用户] "))
    injected = line.split("] ", 1)[1]
    assert injected.endswith("…")
    assert len(injected) <= memory_service.MAX_PROMPT_ITEM_CHARS + 1


@pytest.mark.asyncio
async def test_budget_skips_large_item_and_keeps_small_one() -> None:
    agent = await _create_agent()
    base = datetime.now(timezone.utc)
    small_id = await _add_memory(
        scope="agent",
        agent_id=agent,
        content="部署记录",
        updated_at=base - timedelta(days=10),
    )
    for index in range(5):
        await _add_memory(
            scope="agent",
            agent_id=agent,
            content="部署" + "很" * (300 + index) + f"记录{index}",
            updated_at=base + timedelta(minutes=index),
        )

    block, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="部署"
    )

    assert small_id in ids
    assert len(ids) == 5
    assert "部署记录" in block


@pytest.mark.asyncio
async def test_long_query_still_matches_relevant_memory() -> None:
    agent = await _create_agent()
    expected = await _add_memory(scope="user", content="项目部署使用 systemd")
    await _add_memory(scope="user", content="用户周末去爬山")

    query = (
        "请帮我看看之前那个项目的部署情况，另外顺便说一下今天天气、"
        "明天的安排、周末的计划和下周的日程，还有别的注意事项一起整理一下。"
    )
    _, ids = await memory_service.retrieve_for_prompt(agent_id=agent, query=query)

    assert ids == [expected]


@pytest.mark.asyncio
async def test_english_word_match() -> None:
    agent = await _create_agent()
    expected = await _add_memory(
        scope="user", content="User prefers concise answers"
    )

    block, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="How should you answer?"
    )

    assert ids == [expected]
    assert "concise answers" in block
