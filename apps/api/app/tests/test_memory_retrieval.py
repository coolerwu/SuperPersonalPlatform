from __future__ import annotations

import uuid

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
) -> str:
    item = await memory_service.create_memory(
        scope=scope, content=content, agent_id=agent_id
    )
    assert item is not None
    return item.id


@pytest.mark.asyncio
async def test_all_user_memories_injected_regardless_of_query() -> None:
    agent = await _create_agent()
    first = await _add_memory(scope="user", content="用户喜欢简短回答")
    second = await _add_memory(scope="user", content="用户住在杭州")
    agent_memory = await _add_memory(
        scope="agent", agent_id=agent, content="Agent 使用 systemd 部署"
    )

    block, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="今天天气怎么样"
    )

    assert ids == [first, second]
    assert "用户喜欢简短回答" in block
    assert "用户住在杭州" in block
    assert "## 用户长期记忆" in block
    assert "## Agent 记忆索引" in block
    assert agent_memory in block


@pytest.mark.asyncio
async def test_agent_memory_is_indexed_not_injected_in_full() -> None:
    agent = await _create_agent()
    content = "部署结论：" + "很" * 300 + "TAILMARKER"
    item_id = await _add_memory(scope="agent", agent_id=agent, content=content)

    block, ids = await memory_service.retrieve_for_prompt(agent_id=agent, query="")

    assert ids == []
    assert item_id in block
    assert "TAILMARKER" not in block
    assert "memory_read" in block


@pytest.mark.asyncio
async def test_agent_index_skips_duplicate_of_user_memory() -> None:
    agent = await _create_agent()
    user_id = await _add_memory(scope="user", content="用户喜欢简短回答")
    await _add_memory(scope="agent", agent_id=agent, content="用户喜欢简短回答")

    block, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="简短回答"
    )

    assert ids == [user_id]
    assert block.count("用户喜欢简短回答") == 1
    assert "## Agent 记忆索引" not in block


@pytest.mark.asyncio
async def test_long_user_memory_is_clipped() -> None:
    agent = await _create_agent()
    await _add_memory(scope="user", content="回答" + "很" * 400)

    block, _ = await memory_service.retrieve_for_prompt(agent_id=agent, query="")

    line = next(line for line in block.splitlines() if line.startswith("- "))
    injected = line[2:]
    assert injected.endswith("…")
    assert len(injected) <= memory_service.MAX_USER_PROMPT_ITEM_CHARS + 1


@pytest.mark.asyncio
async def test_user_memory_budget_omits_and_reports() -> None:
    agent = await _create_agent()
    for index in range(25):
        await _add_memory(scope="user", content=f"规则{index}：" + "很" * 300)

    block, ids = await memory_service.retrieve_for_prompt(agent_id=agent, query="")

    assert len(ids) <= memory_service.MAX_USER_PROMPT_ITEMS
    assert "条用户记忆因预算未注入" in block


@pytest.mark.asyncio
async def test_agent_index_truncates_with_search_hint() -> None:
    agent = await _create_agent()
    for index in range(memory_service.MAX_AGENT_INDEX_ITEMS + 5):
        await _add_memory(
            scope="agent", agent_id=agent, content=f"Agent 结论 {index}"
        )

    block, _ = await memory_service.retrieve_for_prompt(agent_id=agent, query="")

    assert "还有" in block
    assert "memory_search" in block


@pytest.mark.asyncio
async def test_agent_index_has_no_total_char_limit() -> None:
    agent = await _create_agent()
    tags = [f"标签{index}" + "x" * 28 for index in range(8)]
    ids: list[str] = []
    for index in range(memory_service.MAX_AGENT_INDEX_ITEMS):
        item = await memory_service.create_memory(
            scope="agent",
            agent_id=agent,
            content=f"结论 {index}",
            tags=tags,
        )
        assert item is not None
        ids.append(item.id)

    block, _ = await memory_service.retrieve_for_prompt(agent_id=agent, query="")

    assert all(item_id in block for item_id in ids)
    assert "未列出" not in block


@pytest.mark.asyncio
async def test_no_memories_returns_empty_block() -> None:
    agent = await _create_agent()

    block, ids = await memory_service.retrieve_for_prompt(
        agent_id=agent, query="今天天气怎么样"
    )

    assert block == ""
    assert ids == []


@pytest.mark.asyncio
async def test_injected_user_memories_update_last_used_at() -> None:
    agent = await _create_agent()
    user_id = await _add_memory(scope="user", content="用户喜欢简短回答")

    await memory_service.retrieve_for_prompt(agent_id=agent, query="随便问问")

    async with SessionLocal() as db:
        stored = await db.get(MemoryItem, user_id)
        assert stored is not None
        assert stored.last_used_at is not None
