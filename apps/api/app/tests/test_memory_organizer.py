from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Agent, Message, Run, RunEvent, Session
from app.services import app_config, memory_organizer
from app.services.run_service import _create_run_in_db


async def _make_agent() -> str:
    async with SessionLocal() as db:
        agent = Agent(
            name="consolidation-test",
            provider_id="default",
            auto_memory=True,
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


async def _turn(session_id: str, *, content: str = "hello") -> str:
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        assert session is not None
        run, _message = await _create_run_in_db(db, session, content, source="manual")
        await db.commit()
        return run.id


async def _set_threshold(value: int) -> None:
    async with SessionLocal() as db:
        row = await app_config.get_or_create(db)
        row.memory_consolidate_every_turns = value
        await db.commit()


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
async def test_user_turns_no_longer_trigger_automatic_organizer(client) -> None:
    agent_id = await _make_agent()
    session_id = await _make_session(agent_id)
    try:
        await _set_threshold(2)
        for _ in range(5):
            await _turn(session_id)
        async with SessionLocal() as db:
            runs = list(
                (
                    await db.scalars(
                        select(Run).where(
                            Run.agent_id == agent_id, Run.source == "memory"
                        )
                    )
                ).all()
            )
            counter = await db.scalar(
                select(Agent.memory_turn_count).where(Agent.id == agent_id)
            )
        assert runs == []
        assert int(counter or 0) == 0
    finally:
        await _set_threshold(0)
        await _cleanup(agent_id)


def test_prompt_matches_frontend_command() -> None:
    frontend = (
        Path(__file__).resolve().parents[4] / "apps/web/src/messageContent.ts"
    )
    if not frontend.exists():
        pytest.skip("frontend source is not available")
    assert memory_organizer.MEMORY_ORGANIZE_PROMPT in frontend.read_text(
        encoding="utf-8"
    )
