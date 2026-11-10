from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, init_db
from app.models import Agent, Message, Provider, Run, Session
from app.services import memory_service
from app.services.channel_service import _maybe_save_explicit_memory


@pytest.fixture(autouse=True)
async def _database():
    await init_db()


async def _fixture(text: str) -> tuple[str, str, str]:
    async with SessionLocal() as db:
        provider = await db.get(Provider, "default")
        if provider is None:
            db.add(Provider(id="default", name="default", base_url="", api_key="", model=""))
        agent = Agent(
            name=f"channel-memory-{uuid.uuid4().hex[:8]}", provider_id="default"
        )
        db.add(agent)
        await db.flush()
        session = Session(agent_id=agent.id)
        db.add(session)
        await db.flush()
        run = Run(
            agent_id=agent.id,
            session_id=session.id,
            source="wechat",
            status="queued",
            input=text,
        )
        db.add(run)
        await db.flush()
        message = Message(session_id=session.id, role="user", content=text, run_id=run.id)
        db.add(message)
        await db.commit()
        return run.id, session.id, message.id


@pytest.mark.asyncio
async def test_wechat_explicit_memory_saves_snapshot() -> None:
    text = "记住：微信也要保存快照"
    run_id, session_id, message_id = await _fixture(text)

    await _maybe_save_explicit_memory(
        text=text,
        message_id=message_id,
        session_id=session_id,
        run_id=run_id,
        created_at=None,
    )

    items = await memory_service.list_memories(scope="user")
    item = next(entry for entry in items if "微信也要保存快照" in entry.content)
    assert item.source_kind == "explicit"
    assert item.snapshot_text == text
    assert item.source_session_id == session_id
    assert item.source_message_id == message_id
    assert item.source_run_id == run_id
    assert item.snapshot_at is not None


@pytest.mark.asyncio
async def test_wechat_plain_message_does_not_save_memory() -> None:
    text = "这个选手最近怎么样"
    run_id, session_id, message_id = await _fixture(text)
    before = len(await memory_service.list_memories(scope="user"))

    await _maybe_save_explicit_memory(
        text=text,
        message_id=message_id,
        session_id=session_id,
        run_id=run_id,
        created_at=None,
    )

    assert len(await memory_service.list_memories(scope="user")) == before
