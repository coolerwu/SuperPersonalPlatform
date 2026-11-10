import asyncio

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery
from app.services import channel_delivery, turn_lease
from app.services.claude_runner import (
    ClaudeTurnRunner,
    DeliveryRequest,
    Turn,
)
from app.services.settings_store import AgentSection, PlatformSettings, ProviderSection
from app.services.turn_stream import TurnStream

SESSION_ID = "11111111-2222-3333-4444-555555555555"
RESUME_SESSION_ID = "66666666-7777-8888-9999-aaaaaaaaaaaa"


class FakeClient:
    def __init__(self, messages=None):
        self._messages = list(messages or [])
        self.queries = []
        self.disconnected = False

    async def connect(self):
        pass

    async def disconnect(self):
        self.disconnected = True

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_messages(self):
        for message in self._messages:
            yield message


class FakeStore:
    def __init__(self, *agents):
        self._agents = [agent for agent in agents if agent is not None]

    async def get(self):
        return PlatformSettings(
            agents=self._agents, channels=[], channel_conversations={}
        )


class ResultMessage:
    def __init__(self, *, result="hello"):
        self.subtype = "success"
        self.terminal_reason = "completed"
        self.result = result
        self.usage = {"input_tokens": 1, "output_tokens": 1}
        self.errors = None
        self.api_error_status = None
        self.is_error = False
        self.origin = None


def make_agent(agent_id="default"):
    return AgentSection(
        id=agent_id,
        name="Assistant",
        provider=ProviderSection(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash[1m]",
            api_key="sk-test",
        ),
    )


async def _cleanup(*session_ids: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelDelivery).where(
                ChannelDelivery.session_id.in_(session_ids)
            )
        )
        await db.commit()
    for session_id in session_ids:
        await turn_lease.remove(session_id)


async def _rows(session_id: str) -> list[ChannelDelivery]:
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(ChannelDelivery).where(
                    ChannelDelivery.session_id == session_id
                )
            )
        )


@pytest.mark.asyncio
async def test_turn_with_delivery_creates_pending_row_and_notifies(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    client = FakeClient([ResultMessage(result="answer")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=SESSION_ID,
        agent_id="default",
        prompt="hi",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "completed"
        rows = await _rows(SESSION_ID)
        assert len(rows) == 1
        row = rows[0]
        assert row.channel_id == "ch1"
        assert row.external_id == "wx-1"
        # complete() fills the answer but leaves the row for the sender.
        assert row.status == "pending"
        assert row.content == "answer"
        assert delivered == [row.id]
        assert not await turn_lease.is_running(SESSION_ID)
    finally:
        await _cleanup(SESSION_ID)


@pytest.mark.asyncio
async def test_resume_reattaches_existing_pending_delivery(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db,
            channel_id="ch1",
            external_id="wx-1",
            session_id=RESUME_SESSION_ID,
        )
        await db.commit()

    client = FakeClient([ResultMessage(result="resumed answer")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=RESUME_SESSION_ID,
        agent_id="default",
        prompt="hi",
        source="resume",
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "completed"
        rows = await _rows(RESUME_SESSION_ID)
        # The existing obligation is reused instead of creating a duplicate.
        assert len(rows) == 1
        assert rows[0].id == delivery_id
        assert rows[0].status == "pending"
        assert rows[0].content == "resumed answer"
        assert delivered == [delivery_id]
        assert not await turn_lease.is_running(RESUME_SESSION_ID)
    finally:
        await _cleanup(RESUME_SESSION_ID)


@pytest.mark.asyncio
async def test_deliver_failure_does_not_mask_terminal_event_or_lease(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)
        raise RuntimeError("wechat send failed")

    client = FakeClient([ResultMessage(result="answer")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    stream = TurnStream()
    queue = await stream.subscribe()
    turn = Turn(
        session_id="99999999-8888-7777-6666-555555555555",
        agent_id="default",
        prompt="hi",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    try:
        outcome = await runner.run(turn, stream)

        assert outcome.status == "completed"
        events = []
        while True:
            event = await asyncio.wait_for(queue.get(), 1)
            events.append(event)
            if event["type"] == "turn_end":
                break
        assert events[-1]["status"] == "completed"
        rows = await _rows(turn.session_id)
        assert len(rows) == 1
        assert rows[0].content == "answer"
        assert delivered == [rows[0].id]
        assert not await turn_lease.is_running(turn.session_id)
    finally:
        await _cleanup(turn.session_id)
