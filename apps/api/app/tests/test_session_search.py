from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db import SessionLocal, _ensure_message_fts, engine, init_db
from app.main import create_app
from app.models import Agent, Message, Provider, Session
from app.services.session_search import read_session_context, search_messages
from app.services.session_tools import build_session_read_tool, build_session_search_tool
from app.services.skill_tools import SkillToolContext


@pytest.fixture(autouse=True)
async def _database():
    await init_db()


async def _ensure_provider() -> None:
    async with SessionLocal() as db:
        if await db.get(Provider, "default") is None:
            db.add(Provider(id="default", name="default", base_url="", api_key="", model=""))
            await db.commit()


async def _create_agent() -> str:
    await _ensure_provider()
    agent_id = f"search-agent-{uuid.uuid4().hex[:12]}"
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


async def _create_session(agent_id: str, title: str) -> str:
    session_id = f"search-session-{uuid.uuid4().hex[:12]}"
    async with SessionLocal() as db:
        db.add(Session(id=session_id, agent_id=agent_id, title=title))
        await db.commit()
    return session_id


async def _add_message(
    session_id: str,
    role: str,
    content: str,
    created_at: datetime | None = None,
) -> str:
    message_id = f"search-message-{uuid.uuid4().hex[:12]}"
    async with SessionLocal() as db:
        kwargs = {}
        if created_at is not None:
            kwargs["created_at"] = created_at
        db.add(
            Message(
                id=message_id,
                session_id=session_id,
                role=role,
                content=content,
                **kwargs,
            )
        )
        await db.commit()
    return message_id


async def _token(prefix: str = "aurora") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


@pytest.mark.asyncio
async def test_search_messages_scopes_to_current_agent() -> None:
    agent_a = await _create_agent()
    agent_b = await _create_agent()
    session_a = await _create_session(agent_a, "A 的部署讨论")
    session_b = await _create_session(agent_b, "B 的部署讨论")
    token = await _token()
    await _add_message(session_a, "user", f"上次的部署方案是 {token} systemd 单进程")
    await _add_message(session_b, "user", f"另一个 Agent 的 {token} 内容")

    hits = await search_messages(agent_id=agent_a, query=token)

    assert len(hits) == 1
    assert hits[0].session_id == session_a
    assert hits[0].session_title == "A 的部署讨论"
    assert token in hits[0].snippet


@pytest.mark.asyncio
async def test_search_messages_matches_chinese_substring() -> None:
    agent = await _create_agent()
    session = await _create_session(agent, "部署记录")
    await _add_message(session, "assistant", "上次决定的部署方案是用 systemd 单进程。")

    hits = await search_messages(agent_id=agent, query="部署方案")

    assert len(hits) == 1
    assert "systemd" in hits[0].snippet


@pytest.mark.asyncio
async def test_search_messages_falls_back_to_like_for_short_query() -> None:
    agent = await _create_agent()
    session = await _create_session(agent, "短词")
    token = await _token("short")
    await _add_message(session, "user", f"关于 AI 助手的记录 {token}")

    hits = await search_messages(agent_id=agent, query="AI")

    assert len(hits) == 1
    assert token in hits[0].snippet


@pytest.mark.asyncio
async def test_read_session_context_returns_messages_around_hit() -> None:
    agent = await _create_agent()
    session = await _create_session(agent, "上下文")
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    ids = [
        await _add_message(
            session,
            "user" if index % 2 == 0 else "assistant",
            f"第 {index} 条消息",
            created_at=base + timedelta(seconds=index),
        )
        for index in range(6)
    ]

    context = await read_session_context(
        agent_id=agent,
        session_id=session,
        message_id=ids[3],
        before=2,
        after=1,
    )

    assert context is not None
    assert [message.message_id for message in context.messages] == [
        ids[1],
        ids[2],
        ids[3],
        ids[4],
    ]
    assert context.target_message_id == ids[3]
    assert context.session_title == "上下文"


@pytest.mark.asyncio
async def test_read_session_context_rejects_other_agent() -> None:
    agent_a = await _create_agent()
    agent_b = await _create_agent()
    session_a = await _create_session(agent_a, "A 的会话")
    await _add_message(session_a, "user", "只有 A 能看到")

    assert (
        await read_session_context(agent_id=agent_b, session_id=session_a)
    ) is None


@pytest.mark.asyncio
async def test_search_reflects_message_delete() -> None:
    agent = await _create_agent()
    session = await _create_session(agent, "删除同步")
    token = await _token("delete")
    message_id = await _add_message(session, "user", f"待删除 {token}")

    assert await search_messages(agent_id=agent, query=token)
    async with SessionLocal() as db:
        await db.execute(delete(Message).where(Message.id == message_id))
        await db.commit()

    assert await search_messages(agent_id=agent, query=token) == []


@pytest.mark.asyncio
async def test_message_fts_backfills_existing_messages() -> None:
    agent = await _create_agent()
    session = await _create_session(agent, "回填")
    token = await _token("backfill")
    first = await _add_message(session, "user", f"已有消息 {token}")

    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP TABLE messages_fts")
        for trigger in ("messages_ai", "messages_ad", "messages_au"):
            await conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")

    second = await _add_message(session, "assistant", f"索引缺失期间的消息 {token}")
    await _ensure_message_fts()

    hits = await search_messages(agent_id=agent, query=token)
    assert {hit.message_id for hit in hits} == {first, second}


@pytest.mark.asyncio
async def test_session_tools_search_and_read() -> None:
    agent = await _create_agent()
    session = await _create_session(agent, "工具会话")
    token = await _token("tool")
    message_id = await _add_message(session, "user", f"工具检索 {token}")
    context = SkillToolContext(
        api_key="", base_url="", agent_id=agent, session_id=session
    )

    search_result = await build_session_search_tool(context).ainvoke({"query": token})
    assert f"session_id={session}" in search_result
    assert token in search_result

    read_result = await build_session_read_tool(context).ainvoke(
        {"session_id": session, "message_id": message_id, "before": 1, "after": 1}
    )
    assert "（命中）" in read_result
    assert token in read_result

    other_agent = await _create_agent()
    denied = await build_session_read_tool(
        SkillToolContext(api_key="", base_url="", agent_id=other_agent)
    ).ainvoke({"session_id": session})
    assert denied.startswith("Error:")


@pytest.mark.asyncio
async def test_session_search_api() -> None:
    agent = await _create_agent()
    other_agent = await _create_agent()
    session = await _create_session(agent, "API 搜索")
    token = await _token("api")
    message_id = await _add_message(session, "user", f"API 检索 {token}")
    other_session = await _create_session(other_agent, "其他 Agent")
    await _add_message(other_session, "user", f"其他 Agent {token}")

    app = create_app(start_worker=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as anonymous:
        unauthorized = await anonymous.get(
            "/api/sessions/search", params={"agent_id": agent, "q": token}
        )
        assert unauthorized.status_code == 401

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post("/api/auth/login", json={"token": "test-token"})
        assert login.status_code == 200
        response = await client.get(
            "/api/sessions/search", params={"agent_id": agent, "q": token}
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["message_id"] == message_id
        assert body[0]["session_title"] == "API 搜索"
        assert token in body[0]["snippet"]

        missing = await client.get(
            "/api/sessions/search", params={"agent_id": "missing-agent", "q": token}
        )
        assert missing.status_code == 404
