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
FAILED_SESSION_ID = "bbbbbbbb-1111-2222-3333-444444444444"
CANCELLED_SESSION_ID = "cccccccc-1111-2222-3333-444444444444"
SHUTDOWN_SESSION_ID = "dddddddd-1111-2222-3333-444444444444"
FILLED_SESSION_ID = "eeeeeeee-1111-2222-3333-444444444444"
EMPTY_REPLY_SESSION_ID = "ffffffff-1111-2222-3333-444444444444"
INTERRUPT_SESSION_ID = "abcdabcd-1111-2222-3333-444444444444"
NO_DELIVERY_SESSION_ID = "abcdeeee-1111-2222-3333-444444444444"
RESUME_REQUEST_SESSION_ID = "abcdffff-1111-2222-3333-444444444444"


class FakeClient:
    def __init__(self, messages=None):
        self._messages = list(messages or [])
        self.queries = []
        self.disconnected = False
        self.queried = asyncio.Event()

    async def connect(self):
        pass

    async def disconnect(self):
        self.disconnected = True

    async def query(self, prompt):
        self.queries.append(prompt)
        self.queried.set()

    async def receive_messages(self):
        for message in self._messages:
            yield message


class HangingClient(FakeClient):
    async def receive_messages(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover - never reached


class InterruptibleClient(FakeClient):
    """Ends the stream without a result as soon as interrupt() arrives."""

    def __init__(self):
        super().__init__()
        self.interrupted = False
        self._released = asyncio.Event()

    async def interrupt(self):
        self.interrupted = True
        self._released.set()

    async def receive_messages(self):
        await self._released.wait()
        return
        yield  # pragma: no cover - never reached


class FakeStore:
    def __init__(self, *agents):
        self._agents = [agent for agent in agents if agent is not None]

    async def get(self):
        return PlatformSettings(
            agents=self._agents, channels=[], channel_conversations={}
        )


class ResultMessage:
    def __init__(
        self,
        *,
        subtype="success",
        terminal_reason="completed",
        result="hello",
        errors=None,
        is_error=False,
    ):
        self.subtype = subtype
        self.terminal_reason = terminal_reason
        self.result = result
        self.usage = {"input_tokens": 1, "output_tokens": 1}
        self.errors = errors
        self.api_error_status = None
        self.is_error = is_error
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
            _, event = await asyncio.wait_for(queue.get(), 1)
            events.append(event)
            if event["type"] == "turn_end":
                break
        assert events[-1]["status"] == "completed"
        rows = await _rows(turn.session_id)
        assert len(rows) == 1
        # The row stays pending so the sweep can retry the failed send.
        assert rows[0].status == "pending"
        assert rows[0].content == "answer"
        assert delivered == [rows[0].id]
        assert not await turn_lease.is_running(turn.session_id)
    finally:
        await _cleanup(turn.session_id)


@pytest.mark.asyncio
async def test_failed_turn_without_output_delivers_fixed_error_text(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    client = FakeClient(
        [
            ResultMessage(
                subtype="error",
                terminal_reason="error",
                result="boom",
                is_error=True,
            )
        ]
    )
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=FAILED_SESSION_ID,
        agent_id="default",
        prompt="hi",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "failed"
        assert outcome.output == ""
        rows = await _rows(FAILED_SESSION_ID)
        assert len(rows) == 1
        # A channel turn must still answer, so the empty output is replaced.
        assert rows[0].status == "pending"
        assert rows[0].content == "（任务执行失败，请稍后重试。）"
        assert delivered == [rows[0].id]
        assert not await turn_lease.is_running(FAILED_SESSION_ID)
    finally:
        await _cleanup(FAILED_SESSION_ID)


@pytest.mark.asyncio
async def test_user_cancelled_turn_drops_delivery_without_sending(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    client = FakeClient([ResultMessage(terminal_reason="aborted_streaming")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=CANCELLED_SESSION_ID,
        agent_id="default",
        prompt="hi",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "cancelled"
        # /stop owes no reply: the obligation is removed, not left pending.
        assert await _rows(CANCELLED_SESSION_ID) == []
        assert delivered == []
        assert not await turn_lease.is_running(CANCELLED_SESSION_ID)
    finally:
        await _cleanup(CANCELLED_SESSION_ID)


@pytest.mark.asyncio
async def test_shutdown_cancellation_keeps_delivery_pending_for_resume(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    client = HangingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=SHUTDOWN_SESSION_ID,
        agent_id="default",
        prompt="hi",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    task = asyncio.create_task(runner.run(turn, TurnStream()))
    try:
        await asyncio.wait_for(client.queried.wait(), 1)
        # registry.close() cancels the worker task during shutdown.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        rows = await _rows(SHUTDOWN_SESSION_ID)
        assert len(rows) == 1
        # Empty and pending so the resumed turn can reattach and send it.
        assert rows[0].status == "pending"
        assert rows[0].content == ""
        assert delivered == []
        assert await turn_lease.is_running(SHUTDOWN_SESSION_ID) is True
    finally:
        if not task.done():
            task.cancel()
        await _cleanup(SHUTDOWN_SESSION_ID)


@pytest.mark.asyncio
async def test_completed_turn_without_output_sends_placeholder(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    client = FakeClient([ResultMessage(result="")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=EMPTY_REPLY_SESSION_ID,
        agent_id="default",
        prompt="hi",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "completed"
        assert outcome.output == ""
        rows = await _rows(EMPTY_REPLY_SESSION_ID)
        assert len(rows) == 1
        # A channel turn must never be completed with an empty body.
        assert rows[0].status == "pending"
        assert rows[0].content == "（无回复）"
        assert delivered == [rows[0].id]
    finally:
        await _cleanup(EMPTY_REPLY_SESSION_ID)


@pytest.mark.asyncio
async def test_resume_does_not_claim_filled_delivery(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db,
            channel_id="ch1",
            external_id="wx-1",
            session_id=FILLED_SESSION_ID,
        )
        await db.commit()
    # A filled row already belongs to the sender; only empty rows are
    # recovery candidates.
    await channel_delivery.complete(delivery_id, "old answer")

    client = FakeClient([ResultMessage(result="new answer")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=FILLED_SESSION_ID,
        agent_id="default",
        prompt="hi",
        source="resume",
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "completed"
        rows = await _rows(FILLED_SESSION_ID)
        assert len(rows) == 1
        assert rows[0].id == delivery_id
        assert rows[0].content == "old answer"
        assert delivered == []
    finally:
        await _cleanup(FILLED_SESSION_ID)


@pytest.mark.asyncio
async def test_interrupted_turn_without_result_discards_delivery(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    client = InterruptibleClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=INTERRUPT_SESSION_ID,
        agent_id="default",
        prompt="hi",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    task = asyncio.create_task(runner.run(turn, TurnStream()))
    try:
        await asyncio.wait_for(client.queried.wait(), 1)
        assert await runner.interrupt(turn.session_id) is True
        outcome = await asyncio.wait_for(task, 1)

        # The stream ended with no result after /stop: nothing is owed.
        assert outcome.status == "failed"
        assert outcome.error == "run ended without result"
        assert client.interrupted is True
        assert await _rows(INTERRUPT_SESSION_ID) == []
        assert delivered == []
        assert not await turn_lease.is_running(INTERRUPT_SESSION_ID)
    finally:
        if not task.done():
            task.cancel()
        await _cleanup(INTERRUPT_SESSION_ID)


@pytest.mark.asyncio
async def test_turn_without_delivery_creates_no_row_or_callback(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    client = FakeClient([ResultMessage(result="web answer")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=NO_DELIVERY_SESSION_ID,
        agent_id="default",
        prompt="hi",
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "completed"
        assert await _rows(NO_DELIVERY_SESSION_ID) == []
        assert delivered == []
        assert not await turn_lease.is_running(NO_DELIVERY_SESSION_ID)
    finally:
        await _cleanup(NO_DELIVERY_SESSION_ID)


@pytest.mark.asyncio
async def test_precreated_delivery_is_discarded_when_the_turn_never_starts(tmp_path):
    await init_db()
    session_id = "12121212-1212-1212-1212-121212121212"
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db, channel_id="ch1", external_id="wx", session_id=session_id
        )
        await db.commit()
    try:
        def unused_factory(options):
            raise AssertionError("client must not be created")

        runner = ClaudeTurnRunner(
            store=FakeStore(),
            home=tmp_path,
            client_factory=unused_factory,
        )
        turn = Turn(
            session_id=session_id,
            agent_id="missing",
            prompt="hi",
            source="wechat",
            delivery=DeliveryRequest("ch1", "wx", delivery_id),
        )
        outcome = await runner.run(turn, TurnStream())
        assert outcome.status == "failed"
        assert await channel_delivery.get(delivery_id) is None
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(ChannelDelivery.id == delivery_id)
            )
            await db.commit()


@pytest.mark.asyncio
async def test_precreated_queued_delivery_is_completed_and_sent(tmp_path):
    await init_db()
    session_id = "13131313-1313-1313-1313-131313131313"
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db, channel_id="ch1", external_id="wx", session_id=session_id
        )
        await db.commit()
    client = FakeClient([ResultMessage(result="hello")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    turn = Turn(
        session_id=session_id,
        agent_id="default",
        prompt="hi",
        source="wechat",
        delivery=DeliveryRequest("ch1", "wx", delivery_id),
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "completed"
        row = await channel_delivery.get(delivery_id)
        assert row is not None
        # The pre-created queued row was promoted and then filled in place;
        # no second obligation was created.
        assert row.status == "pending"
        assert row.content == "hello"
        assert delivered == [delivery_id]
    finally:
        await _cleanup(session_id)


@pytest.mark.asyncio
async def test_resume_never_creates_delivery_even_when_requested(tmp_path):
    await init_db()
    delivered: list[int] = []

    async def deliver(delivery_id: int) -> None:
        delivered.append(delivery_id)

    client = FakeClient([ResultMessage(result="resumed answer")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        deliver_callback=deliver,
    )
    # A resumed turn must attach to an obligation created before the crash;
    # creating another one here would double reply.
    turn = Turn(
        session_id=RESUME_REQUEST_SESSION_ID,
        agent_id="default",
        prompt="hi",
        source="resume",
        delivery=DeliveryRequest(channel_id="ch1", external_id="wx-1"),
    )
    try:
        outcome = await runner.run(turn, TurnStream())

        assert outcome.status == "completed"
        assert await _rows(RESUME_REQUEST_SESSION_ID) == []
        assert delivered == []
    finally:
        await _cleanup(RESUME_REQUEST_SESSION_ID)
