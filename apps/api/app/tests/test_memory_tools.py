from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, init_db
from app.models import Agent, Provider
from app.services import memory_service, memory_tools  # noqa: F401 - registers memory tools
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext
from app.services.skills import DEFAULT_SKILLS, scan_skills, seed_default_skills


@pytest.fixture(autouse=True)
async def _database():
    await init_db()


async def _ensure_provider() -> None:
    async with SessionLocal() as db:
        provider = await db.get(Provider, "default")
        if provider is None:
            db.add(Provider(id="default", name="default", base_url="", api_key="", model=""))
            await db.commit()


async def _create_agent() -> str:
    await _ensure_provider()
    agent_id = f"agent-{uuid.uuid4().hex[:12]}"
    async with SessionLocal() as db:
        db.add(
            Agent(
                id=agent_id,
                name=agent_id,
                instructions="",
                provider_id="default",
                enabled_skills=["memory-organizer"],
            )
        )
        await db.commit()
    return agent_id


def _context(agent_id: str) -> SkillToolContext:
    return SkillToolContext(api_key="", base_url="", agent_id=agent_id)


def _tools(context: SkillToolContext):
    return {
        tool.name: tool
        for tool in SKILL_TOOL_REGISTRY.resolve_names(
            ["memory_list", "memory_update", "memory_delete"], context
        )
    }


def test_memory_organizer_skill_declares_memory_tools(tmp_path) -> None:
    seed_default_skills(tmp_path)
    skills = {skill.name: skill for skill in scan_skills(tmp_path).skills}
    assert "memory-organizer" in DEFAULT_SKILLS
    assert skills["memory-organizer"].include_tools == (
        "memory_list",
        "memory_update",
        "memory_delete",
    )
    assert {"memory_list", "memory_update", "memory_delete"} <= {
        spec.name for spec in SKILL_TOOL_REGISTRY.specs()
    }


@pytest.mark.asyncio
async def test_memory_list_scopes_to_user_and_current_agent() -> None:
    agent_a = await _create_agent()
    agent_b = await _create_agent()
    user_item = await memory_service.create_memory(scope="user", content="用户偏好先给结论")
    item_a = await memory_service.create_memory(
        scope="agent", agent_id=agent_a, content="Agent A 负责交易分析"
    )
    item_b = await memory_service.create_memory(
        scope="agent", agent_id=agent_b, content="Agent B 负责写作"
    )
    tools = _tools(_context(agent_a))

    everything = await tools["memory_list"].ainvoke({"scope": "all"})
    assert user_item.id in everything
    assert item_a.id in everything
    assert item_b.id not in everything

    agent_only = await tools["memory_list"].ainvoke({"scope": "agent"})
    assert item_a.id in agent_only
    assert user_item.id not in agent_only
    assert item_b.id not in agent_only


@pytest.mark.asyncio
async def test_memory_update_and_delete_reject_other_agents() -> None:
    agent_a = await _create_agent()
    agent_b = await _create_agent()
    user_item = await memory_service.create_memory(scope="user", content="用户喜欢简洁回答")
    item_a = await memory_service.create_memory(
        scope="agent", agent_id=agent_a, content="Agent A 的旧偏好"
    )
    item_b = await memory_service.create_memory(
        scope="agent", agent_id=agent_b, content="Agent B 的记忆"
    )
    tools = _tools(_context(agent_a))

    blocked = await tools["memory_update"].ainvoke(
        {"memory_id": item_b.id, "content": "不应该改到"}
    )
    assert blocked == "Error: memory not found"
    blocked = await tools["memory_delete"].ainvoke({"memory_id": item_b.id})
    assert blocked == "Error: memory not found"
    assert await memory_service.get_memory(item_b.id) is not None

    updated = await tools["memory_update"].ainvoke(
        {"memory_id": item_a.id, "content": "Agent A 的偏好：先给结论", "tags": ["style"]}
    )
    assert "已更新记忆" in updated
    stored = await memory_service.get_memory(item_a.id)
    assert stored is not None
    assert stored.content == "Agent A 的偏好：先给结论"
    assert stored.tags == ["style"]

    deleted = await tools["memory_delete"].ainvoke({"memory_id": user_item.id})
    assert "已删除记忆" in deleted
    assert await memory_service.get_memory(user_item.id) is None


@pytest.mark.asyncio
async def test_memory_list_validates_scope_and_limit() -> None:
    agent_id = await _create_agent()
    tools = _tools(_context(agent_id))

    assert (await tools["memory_list"].ainvoke({"scope": "other"})).startswith("Error:")
    assert (await tools["memory_list"].ainvoke({"limit": 0})).startswith("Error:")
    assert (await tools["memory_list"].ainvoke({"offset": -1})).startswith("Error:")
    empty = _tools(SkillToolContext(api_key="", base_url=""))
    assert (await empty["memory_list"].ainvoke({"scope": "agent"})).startswith("Error:")
