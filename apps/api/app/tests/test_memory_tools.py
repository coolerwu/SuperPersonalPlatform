from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, init_db
from app.models import Agent, Message, Provider, Run, Session
from app.services import memory_service, memory_tools  # noqa: F401 - registers memory tools
from app.services.memory_tools import (
    build_memory_read_tool,
    build_memory_save_tool,
    build_memory_search_tool,
)
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


async def _create_run(agent_id: str) -> tuple[str, str, str]:
    async with SessionLocal() as db:
        session = Session(agent_id=agent_id)
        db.add(session)
        await db.flush()
        run = Run(
            agent_id=agent_id,
            session_id=session.id,
            source="manual",
            status="queued",
            input="hi",
        )
        db.add(run)
        await db.flush()
        message = Message(session_id=session.id, role="user", content="hi", run_id=run.id)
        db.add(message)
        await db.commit()
        return run.id, session.id, message.id


def _context(
    agent_id: str,
    *,
    run_id: str = "",
    session_id: str = "",
    allow_memory_save: bool = False,
) -> SkillToolContext:
    return SkillToolContext(
        api_key="",
        base_url="",
        agent_id=agent_id,
        session_id=session_id,
        run_id=run_id,
        allow_memory_save=allow_memory_save,
    )


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
async def test_memory_list_only_returns_current_agent_memory() -> None:
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

    listed = await tools["memory_list"].ainvoke({})
    assert item_a.id in listed
    assert user_item.id not in listed
    assert item_b.id not in listed


@pytest.mark.asyncio
async def test_memory_update_and_delete_reject_other_agents_and_user() -> None:
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

    assert (
        await tools["memory_update"].ainvoke(
            {"memory_id": item_b.id, "content": "不应该改到"}
        )
        == "Error: memory not found"
    )
    assert (
        await tools["memory_delete"].ainvoke({"memory_id": item_b.id})
        == "Error: memory not found"
    )
    assert (
        await tools["memory_update"].ainvoke(
            {"memory_id": user_item.id, "content": "不应该改到"}
        )
        == "Error: memory not found"
    )
    assert (
        await tools["memory_delete"].ainvoke({"memory_id": user_item.id})
        == "Error: memory not found"
    )
    assert await memory_service.get_memory(item_b.id) is not None
    assert await memory_service.get_memory(user_item.id) is not None

    updated = await tools["memory_update"].ainvoke(
        {"memory_id": item_a.id, "content": "Agent A 的偏好：先给结论", "tags": ["style"]}
    )
    assert "已更新记忆" in updated
    stored = await memory_service.get_memory(item_a.id)
    assert stored is not None
    assert stored.content == "Agent A 的偏好：先给结论"
    assert stored.tags == ["style"]

    deleted = await tools["memory_delete"].ainvoke({"memory_id": item_a.id})
    assert "已删除记忆" in deleted
    assert await memory_service.get_memory(item_a.id) is None


@pytest.mark.asyncio
async def test_memory_list_validates_limit_and_agent() -> None:
    agent_id = await _create_agent()
    tools = _tools(_context(agent_id))

    assert (await tools["memory_list"].ainvoke({"limit": 0})).startswith("Error:")
    assert (await tools["memory_list"].ainvoke({"offset": -1})).startswith("Error:")
    empty = _tools(SkillToolContext(api_key="", base_url=""))
    assert (await empty["memory_list"].ainvoke({})).startswith("Error:")


@pytest.mark.asyncio
async def test_memory_save_writes_agent_memory_with_provenance() -> None:
    agent_id = await _create_agent()
    run_id, session_id, message_id = await _create_run(agent_id)
    tool = build_memory_save_tool(
        _context(
            agent_id,
            run_id=run_id,
            session_id=session_id,
            allow_memory_save=True,
        )
    )

    result = await tool.ainvoke({"content": "项目用 Python 3.12", "tags": ["python"]})
    assert "已保存 Agent 记忆" in result

    items = await memory_service.list_memories(scope="agent", agent_id=agent_id)
    assert len(items) == 1
    item = items[0]
    assert item.scope == "agent"
    assert item.agent_id == agent_id
    assert item.source_kind == "agent"
    assert item.source_run_id == run_id
    assert item.source_session_id == session_id
    assert item.source_message_id == message_id
    assert item.snapshot_text is None
    user_items = await memory_service.list_memories(scope="user")
    assert not any(entry.content == "项目用 Python 3.12" for entry in user_items)


@pytest.mark.asyncio
async def test_memory_search_and_read_only_touch_current_agent() -> None:
    agent_id = await _create_agent()
    other_agent = await _create_agent()
    run_id, session_id, _message_id = await _create_run(agent_id)
    item = await memory_service.create_memory(
        scope="agent",
        agent_id=agent_id,
        content="项目使用 systemd 部署",
        tags=["部署"],
    )
    other = await memory_service.create_memory(
        scope="agent",
        agent_id=other_agent,
        content="别的 Agent 的记忆",
    )
    user_item = await memory_service.create_memory(
        scope="user", content="用户偏好先给结论"
    )
    context = _context(agent_id, run_id=run_id, session_id=session_id)

    search = build_memory_search_tool(context)
    result = await search.ainvoke({"query": "systemd"})
    assert item is not None and item.id in result
    assert other is not None and other.id not in result
    assert "memory_read" in result

    read = build_memory_read_tool(context)
    detail = await read.ainvoke({"memory_id": item.id})
    assert "项目使用 systemd 部署" in detail
    assert (await read.ainvoke({"memory_id": other.id})) == "Error: memory not found"
    assert (
        await read.ainvoke({"memory_id": user_item.id})
    ) == "Error: memory not found"

    stored = await memory_service.get_memory(item.id)
    assert stored is not None
    assert stored.last_used_at is not None


@pytest.mark.asyncio
async def test_memory_save_requires_enablement_and_run() -> None:
    agent_id = await _create_agent()
    run_id, session_id, _message_id = await _create_run(agent_id)

    disabled = build_memory_save_tool(
        _context(agent_id, run_id=run_id, session_id=session_id)
    )
    assert (await disabled.ainvoke({"content": "项目用 Python 3.12"})).startswith(
        "Error: memory_save is disabled"
    )

    no_run = build_memory_save_tool(
        _context(agent_id, allow_memory_save=True)
    )
    assert (await no_run.ainvoke({"content": "项目用 Python 3.12"})).startswith(
        "Error: current agent or run"
    )


@pytest.mark.asyncio
async def test_memory_save_rejects_long_content_and_caps_per_run() -> None:
    agent_id = await _create_agent()
    run_id, session_id, _message_id = await _create_run(agent_id)
    tool = build_memory_save_tool(
        _context(
            agent_id,
            run_id=run_id,
            session_id=session_id,
            allow_memory_save=True,
        )
    )

    too_long = await tool.ainvoke({"content": "字" * 321})
    assert "at most 320" in too_long

    for index in range(5):
        result = await tool.ainvoke({"content": f"可复用结论 {index}"})
        assert "已保存 Agent 记忆" in result
    blocked = await tool.ainvoke({"content": "第六条"})
    assert "already saved 5 memories" in blocked
