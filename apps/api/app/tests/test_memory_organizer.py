from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Agent, Message, Run, RunEvent, Session
from app.services import app_config, memory_organizer
from app.services.run_service import _create_run_in_db


async def _make_agent(*, auto_memory: bool = True) -> str:
    async with SessionLocal() as db:
        agent = Agent(
            name="consolidation-test",
            provider_id="default",
            auto_memory=auto_memory,
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return agent.id


async def _make_session(agent_id: str) -> str:
    async with SessionLocal() as db:
        session = Session(agent_id=agent_id)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session.id


async def _turn(
    session_id: str,
    *,
    content: str = "hello",
    source: str = "manual",
) -> str:
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        assert session is not None
        run, _message = await _create_run_in_db(db, session, content, source=source)
        await db.commit()
        return run.id


async def _set_threshold(value: int) -> None:
    async with SessionLocal() as db:
        row = await app_config.get_or_create(db)
        row.memory_consolidate_every_turns = value
        await db.commit()


async def _counter(agent_id: str) -> int:
    async with SessionLocal() as db:
        value = await db.scalar(
            select(Agent.memory_turn_count).where(Agent.id == agent_id)
        )
        return int(value or 0)


async def _memory_runs(agent_id: str) -> list[Run]:
    async with SessionLocal() as db:
        return list(
            (
                await db.scalars(
                    select(Run)
                    .where(Run.agent_id == agent_id, Run.source == "memory")
                    .order_by(Run.created_at)
                )
            ).all()
        )


async def _cleanup(agent_id: str) -> None:
    async with SessionLocal() as db:
        run_ids = list(
            (await db.scalars(select(Run.id).where(Run.agent_id == agent_id))).all()
        )
        session_ids = list(
            (await db.scalars(select(Session.id).where(Session.agent_id == agent_id))).all()
        )
        if run_ids:
            await db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
            await db.execute(delete(Run).where(Run.id.in_(run_ids)))
        if session_ids:
            await db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
            await db.execute(delete(Session).where(Session.id.in_(session_ids)))
        await db.execute(delete(Agent).where(Agent.id == agent_id))
        await db.commit()


@pytest.mark.asyncio
async def test_user_turns_trigger_organizer_at_threshold(client) -> None:
    agent_id = await _make_agent()
    session_id = await _make_session(agent_id)
    try:
        await _set_threshold(3)

        await _turn(session_id)
        await _turn(session_id)
        assert await _counter(agent_id) == 2
        assert await _memory_runs(agent_id) == []

        await _turn(session_id)
        assert await _counter(agent_id) == 0

        runs = await _memory_runs(agent_id)
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "queued"
        assert run.input == memory_organizer.MEMORY_ORGANIZE_PROMPT
        async with SessionLocal() as db:
            organizer_session = await db.get(Session, run.session_id)
        assert organizer_session is not None
        assert organizer_session.origin == "memory"
        assert organizer_session.title == memory_organizer.MEMORY_SESSION_TITLE

        await _turn(session_id)
        assert await _counter(agent_id) == 1
    finally:
        await _set_threshold(0)
        await _cleanup(agent_id)


@pytest.mark.asyncio
async def test_manual_organize_prompt_resets_counter(client) -> None:
    agent_id = await _make_agent()
    session_id = await _make_session(agent_id)
    try:
        await _set_threshold(10)
        for _ in range(4):
            await _turn(session_id)
        assert await _counter(agent_id) == 4

        await _turn(session_id, content=memory_organizer.MEMORY_ORGANIZE_PROMPT)
        assert await _counter(agent_id) == 0
        assert await _memory_runs(agent_id) == []
    finally:
        await _set_threshold(0)
        await _cleanup(agent_id)


@pytest.mark.asyncio
async def test_only_user_sources_are_counted(client) -> None:
    agent_id = await _make_agent()
    session_id = await _make_session(agent_id)
    try:
        await _set_threshold(10)

        await _turn(session_id, source="cron")
        await _turn(session_id, source="retry")
        await _turn(session_id, source="memory")
        assert await _counter(agent_id) == 0

        await _turn(session_id, source="wechat")
        assert await _counter(agent_id) == 1
    finally:
        await _set_threshold(0)
        await _cleanup(agent_id)


@pytest.mark.asyncio
async def test_threshold_zero_disables(client) -> None:
    agent_id = await _make_agent()
    session_id = await _make_session(agent_id)
    try:
        await _set_threshold(0)
        for _ in range(5):
            await _turn(session_id)
        assert await _counter(agent_id) == 0
        assert await _memory_runs(agent_id) == []
    finally:
        await _cleanup(agent_id)


@pytest.mark.asyncio
async def test_auto_memory_off_disables(client) -> None:
    agent_id = await _make_agent(auto_memory=False)
    session_id = await _make_session(agent_id)
    try:
        await _set_threshold(2)
        for _ in range(3):
            await _turn(session_id)
        assert await _counter(agent_id) == 0
        assert await _memory_runs(agent_id) == []
    finally:
        await _set_threshold(0)
        await _cleanup(agent_id)


def test_settings_api_round_trip(client) -> None:
    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert isinstance(initial.json()["memory_consolidate_every_turns"], int)

    try:
        saved = client.put(
            "/api/settings",
            json={"session_retention_days": 30, "memory_consolidate_every_turns": 5},
        )
        assert saved.status_code == 200
        assert saved.json()["memory_consolidate_every_turns"] == 5

        rejected = client.put(
            "/api/settings",
            json={"session_retention_days": 30, "memory_consolidate_every_turns": 5000},
        )
        assert rejected.status_code == 422
    finally:
        restored = client.put(
            "/api/settings",
            json={"session_retention_days": 30, "memory_consolidate_every_turns": 0},
        )
        assert restored.status_code == 200
        assert restored.json()["memory_consolidate_every_turns"] == 0


def test_prompt_matches_frontend_command() -> None:
    frontend = (
        Path(__file__).resolve().parents[4] / "apps/web/src/messageContent.ts"
    )
    if not frontend.exists():
        pytest.skip("frontend source is not available")
    assert memory_organizer.MEMORY_ORGANIZE_PROMPT in frontend.read_text(
        encoding="utf-8"
    )
