import asyncio

import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery
from app.services import channel_delivery
from app.services.claude_runner import DeliveryRequest, Turn
from app.services.session_registry import (
    AgentDeletingError,
    SessionDeletingError,
    SessionRegistry,
)


async def wait_until(predicate, timeout=2.0):
    async def _poll():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_poll(), timeout)


async def drain_terminal(stream):
    queue = await stream.subscribe()
    events = []
    while True:
        _, event = await asyncio.wait_for(queue.get(), 1.0)
        events.append(event)
        if event.get("type") == "turn_end":
            return events


class FakeRunner:
    """Runner whose turns block on a per-prompt gate until the test releases it."""

    def __init__(self):
        self.started = []
        self.finished = []
        self.interrupted = []
        self.interrupt_turn_ids: list[str | None] = []
        self.closed = []
        self.after_turns = []
        self.interrupt_result = False
        self.active = 0
        self.max_active = 0
        self._gates: dict[str, asyncio.Event] = {}

    def gate(self, prompt: str) -> asyncio.Event:
        return self._gates.setdefault(prompt, asyncio.Event())

    def release(self, prompt: str) -> None:
        self.gate(prompt).set()

    async def run(self, turn, stream, *, defer_eviction=False):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.append(turn.prompt)
        try:
            await self.gate(turn.prompt).wait()
            stream.publish({"type": "assistant_delta", "delta": turn.prompt})
        finally:
            stream.close()
            self.active -= 1
            self.finished.append(turn.prompt)

    async def after_turn(self, session_id, agent_id=None):
        self.after_turns.append((session_id, agent_id))

    async def interrupt(self, session_id, agent_id=None, turn_id=None):
        self.interrupted.append(session_id)
        self.interrupt_turn_ids.append(turn_id)
        return self.interrupt_result

    async def close(self, session_id):
        self.closed.append(session_id)


def turn(session_id="s1", prompt="a"):
    return Turn(session_id=session_id, agent_id="default", prompt=prompt)


@pytest.mark.asyncio
async def test_turns_in_same_session_are_serialized():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    first = await registry.submit(turn(prompt="a"))
    second = await registry.submit(turn(prompt="b"))

    assert first != second
    assert registry.stream(first) is not registry.stream(second)

    await wait_until(lambda: runner.started == ["a"])
    assert registry.is_busy("s1")
    assert registry.current_turn_id("s1") == first

    runner.release("a")
    await wait_until(lambda: runner.started == ["a", "b"])
    runner.release("b")
    await wait_until(lambda: runner.finished == ["a", "b"])

    assert runner.max_active == 1
    assert registry.stream(first).closed
    assert registry.stream(second).closed
    await wait_until(lambda: not registry.is_busy("s1"))
    assert registry.current_turn_id("s1") is None
    await registry.close()


@pytest.mark.asyncio
async def test_cross_session_runs_are_bounded_by_concurrency():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    first = await registry.submit(turn(session_id="s1", prompt="a"))
    second = await registry.submit(turn(session_id="s2", prompt="b"))

    await wait_until(lambda: len(runner.started) == 1)
    assert runner.max_active == 1
    runner.release("a")
    runner.release("b")
    await wait_until(lambda: len(runner.finished) == 2)
    assert runner.max_active == 1

    assert registry.stream(first).closed
    assert registry.stream(second).closed
    await registry.close()


@pytest.mark.asyncio
async def test_idle_worker_and_queue_are_reclaimed():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    runner.release("a")
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.finished == ["a"])
    await wait_until(lambda: not registry._workers and not registry._queues)
    assert registry._current == {}

    # The retired worker must not strand a later submit for the same session.
    runner.release("b")
    await registry.submit(turn(prompt="b"))
    await wait_until(lambda: runner.finished == ["a", "b"])
    await registry.close()


@pytest.mark.asyncio
async def test_evicts_oldest_closed_streams_when_capacity_exceeded():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1, max_streams=2)
    ids = []
    for prompt in ("a", "b", "c", "d"):
        runner.release(prompt)
        ids.append(await registry.submit(turn(session_id=prompt, prompt=prompt)))
        await wait_until(lambda p=prompt: p in runner.finished)

    assert registry.stream(ids[0]) is None
    assert registry.stream(ids[1]) is None
    assert registry.stream(ids[2]) is not None
    assert registry.stream(ids[3]) is not None
    await registry.close()


@pytest.mark.asyncio
async def test_running_stream_is_never_evicted():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2, max_streams=1)
    running_id = await registry.submit(turn(session_id="slow", prompt="slow"))
    await wait_until(lambda: runner.started == ["slow"])

    for prompt in ("a", "b"):
        runner.release(prompt)
        await registry.submit(turn(session_id=prompt, prompt=prompt))
        await wait_until(lambda p=prompt: p in runner.finished)

    assert registry.stream(running_id) is not None
    assert not registry.stream(running_id).closed

    runner.release("slow")
    await wait_until(lambda: "slow" in runner.finished)
    await registry.close()


@pytest.mark.asyncio
async def test_interrupt_idle_session_returns_false():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    result = await registry.interrupt("idle")
    assert result.cancelled is False
    assert result.cancelled_turn_ids == ()
    assert not result
    await registry.close()
    assert runner.interrupted == ["idle"]


@pytest.mark.asyncio
async def test_interrupt_delegates_to_active_runner():
    runner = FakeRunner()
    runner.interrupt_result = True
    registry = SessionRegistry(runner, concurrency=2)
    running_id = await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])

    result = await registry.interrupt("s1")
    assert result.cancelled is True
    assert result.cancelled_turn_ids == (running_id,)
    assert runner.interrupted == ["s1"]

    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


@pytest.mark.asyncio
async def test_interrupt_cancels_queued_turns_and_terminates_their_streams():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    queued_id = await registry.submit(turn(prompt="b"))
    await wait_until(lambda: runner.started == ["a"])

    queued_stream = registry.stream(queued_id)
    result = await registry.interrupt("s1")
    assert result.cancelled is True
    assert queued_id in result.cancelled_turn_ids
    assert runner.interrupted == ["s1"]
    assert queued_stream.closed
    assert await drain_terminal(queued_stream) == [
        {"type": "turn_end", "status": "cancelled"}
    ]

    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


@pytest.mark.asyncio
async def test_interrupt_before_worker_starts_cancels_queued_turn():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    turn_id = await registry.submit(turn(prompt="a"))
    stream = registry.stream(turn_id)

    assert (await registry.interrupt("s1")).cancelled is True
    assert stream.closed
    assert await drain_terminal(stream) == [
        {"type": "turn_end", "status": "cancelled"}
    ]

    await wait_until(lambda: not registry._workers)
    assert runner.started == []
    await registry.close()


@pytest.mark.asyncio
async def test_interrupt_cancels_turn_waiting_for_concurrency_permit():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    await registry.submit(turn(session_id="s1", prompt="a"))
    await wait_until(lambda: runner.started == ["a"])
    waiting_id = await registry.submit(turn(session_id="s2", prompt="b"))
    await wait_until(
        lambda: "s2" in registry._pending and "s2" not in registry._started
    )
    waiting_stream = registry.stream(waiting_id)

    assert (await registry.interrupt("s2")).cancelled is True
    assert waiting_stream.closed
    assert await drain_terminal(waiting_stream) == [
        {"type": "turn_end", "status": "cancelled"}
    ]

    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await wait_until(
        lambda: "s2" not in registry._pending
        and "s2" not in registry._workers
        and "s2" not in registry._queues
    )
    assert "s2" not in registry._workers
    assert "s2" not in registry._queues
    assert "b" not in runner.started
    await registry.close()


@pytest.mark.asyncio
async def test_submit_after_close_raises_and_close_is_idempotent():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.close()
    await registry.close()
    with pytest.raises(RuntimeError, match="registry is closed"):
        await registry.submit(turn())


@pytest.mark.asyncio
async def test_interrupt_discards_a_queued_delivery():
    await init_db()
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db, channel_id="ch1", external_id="wx", session_id="s1"
        )
        await db.commit()
    queued = Turn(
        session_id="s1",
        agent_id="default",
        prompt="b",
        delivery=DeliveryRequest("ch1", "wx", delivery_id),
    )
    await registry.submit(queued)
    try:
        assert (await registry.interrupt("s1")).cancelled is True
        assert await channel_delivery.get(delivery_id) is None
    finally:
        runner.release("a")
        await wait_until(lambda: runner.finished == ["a"])
        await registry.close()


@pytest.mark.asyncio
async def test_interrupt_binds_the_turn_id_captured_before_discarding(monkeypatch):
    from app.services import session_registry as registry_module

    await init_db()
    runner = FakeRunner()
    runner.interrupt_result = True
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])
    first_id = registry.current_turn_id("s1")
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db, channel_id="ch1", external_id="wx", session_id="s1"
        )
        await db.commit()
    queued = Turn(
        session_id="s1",
        agent_id="default",
        prompt="b",
        delivery=DeliveryRequest("ch1", "wx", delivery_id),
    )
    await registry.submit(queued)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_discard(_turn):
        entered.set()
        await release.wait()

    monkeypatch.setattr(registry_module, "_discard_delivery", blocking_discard)
    interrupting = asyncio.create_task(registry.interrupt("s1"))
    try:
        await wait_until(entered.is_set)
        # The running turn ends and a new turn starts while interrupt is
        # still inside the queued delivery discard await.
        runner.release("a")
        await wait_until(lambda: runner.finished == ["a"])
        await wait_until(lambda: not registry._workers)

        third_id = await registry.submit(turn(prompt="c"))
        await wait_until(lambda: runner.started == ["a", "c"])

        release.set()
        assert (await asyncio.wait_for(interrupting, 1)).cancelled is True
        assert runner.interrupt_turn_ids[-1] == first_id
        assert runner.interrupt_turn_ids[-1] != third_id

        runner.release("c")
        await wait_until(lambda: runner.finished == ["a", "c"])
    finally:
        release.set()
        if not interrupting.done():
            await asyncio.wait_for(interrupting, 1)
        await registry.close()
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(ChannelDelivery.id == delivery_id)
            )
            await db.commit()


@pytest.mark.asyncio
async def test_close_keeps_a_queued_delivery_for_startup():
    await init_db()
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db, channel_id="ch1", external_id="wx", session_id="s1"
        )
        await db.commit()
    queued = Turn(
        session_id="s1",
        agent_id="default",
        prompt="b",
        delivery=DeliveryRequest("ch1", "wx", delivery_id),
    )
    await registry.submit(queued)
    try:
        await registry.close()
        assert await channel_delivery.get(delivery_id) is not None
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(ChannelDelivery.id == delivery_id)
            )
            await db.commit()


@pytest.mark.asyncio
async def test_close_terminates_queued_streams():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    queued_id = await registry.submit(turn(prompt="b"))
    await wait_until(lambda: runner.started == ["a"])
    queued_stream = registry.stream(queued_id)

    await registry.close()

    assert registry.stream(queued_id) is None
    assert queued_stream.closed
    assert await drain_terminal(queued_stream) == [
        {"type": "turn_end", "status": "cancelled"}
    ]


@pytest.mark.asyncio
async def test_is_agent_busy_tracks_queued_and_running_turns():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    first = Turn(session_id="s1", agent_id="agent-a", prompt="a")
    queued = Turn(session_id="s2", agent_id="agent-a", prompt="b")
    other = Turn(session_id="s3", agent_id="agent-b", prompt="c")
    await registry.submit(first)
    await registry.submit(queued)
    await registry.submit(other)
    await wait_until(lambda: "a" in runner.started)

    # The queued turn has no transcript yet, but it still protects its Agent.
    assert registry.is_agent_busy("agent-a")
    assert registry.is_agent_busy("agent-b")
    assert not registry.is_agent_busy("agent-c")

    for prompt in ("a", "b", "c"):
        runner.release(prompt)
    await wait_until(lambda: len(runner.finished) == 3)
    await wait_until(lambda: not registry.is_agent_busy("agent-a"))
    await registry.close()


@pytest.mark.asyncio
async def test_stream_is_bound_to_session_and_close_session_delegates():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    turn_id = await registry.submit(turn("s1", "a"))

    assert registry.stream(turn_id, "s1") is not None
    assert registry.stream(turn_id, "other") is None
    assert registry.stream(turn_id) is not None

    await registry.close_session("s1")
    assert runner.closed == ["s1"]

    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


@pytest.mark.asyncio
async def test_close_agent_clients_skips_busy_and_other_agents():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    idle = Turn(session_id="idle", agent_id="agent-a", prompt="idle")
    busy = Turn(session_id="busy", agent_id="agent-a", prompt="busy")
    other = Turn(session_id="other", agent_id="agent-b", prompt="other")

    await registry.submit(idle)
    runner.release("idle")
    await wait_until(lambda: "idle" in runner.finished)

    await registry.submit(busy)
    await wait_until(lambda: "busy" in runner.started)

    await registry.submit(other)
    runner.release("other")
    await wait_until(lambda: "other" in runner.finished)

    assert await registry.close_agent_clients("agent-a") == 1
    assert runner.closed == ["idle"]

    runner.release("busy")
    await wait_until(lambda: "busy" in runner.finished)
    await registry.close()


@pytest.mark.asyncio
async def test_after_turn_runs_after_the_session_turn_finishes():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    runner.release("a")
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.finished == ["a"])
    await wait_until(lambda: runner.after_turns == [("s1", "default")])
    await registry.close()


@pytest.mark.asyncio
async def test_interrupt_passes_the_running_turn_id():
    runner = FakeRunner()
    runner.interrupt_result = True
    registry = SessionRegistry(runner, concurrency=2)
    turn_id = await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])

    assert (await registry.interrupt("s1")).cancelled is True
    assert runner.interrupt_turn_ids == [turn_id]

    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


class SlowAfterTurnRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.after_started = asyncio.Event()
        self.after_release = asyncio.Event()

    async def after_turn(self, session_id, agent_id=None):
        self.after_started.set()
        await self.after_release.wait()
        self.after_turns.append((session_id, agent_id))


@pytest.mark.asyncio
async def test_interrupt_does_not_cancel_a_finishing_worker():
    runner = SlowAfterTurnRunner()
    registry = SessionRegistry(runner, concurrency=1)
    await registry.submit(turn(prompt="a"))
    runner.release("a")
    await wait_until(lambda: runner.after_started.is_set())

    await registry.submit(turn(prompt="b"))
    assert (await registry.interrupt("s1")).cancelled is True

    runner.after_release.set()
    await wait_until(lambda: runner.after_turns == [("s1", "default")])
    await wait_until(lambda: not registry._workers)
    assert runner.started == ["a"]
    await registry.close()


@pytest.mark.asyncio
async def test_begin_delete_rejects_new_turns_until_finished():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    try:
        assert registry.begin_delete("s1") is True
        with pytest.raises(SessionDeletingError):
            await registry.submit(turn(prompt="blocked"))

        # The worker never saw the refused turn.
        await asyncio.sleep(0)
        assert runner.started == []

        registry.finish_delete("s1")
        runner.release("after-delete")
        await registry.submit(turn(prompt="after-delete"))
        await wait_until(lambda: runner.finished == ["after-delete"])
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_begin_delete_refuses_a_busy_session():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    try:
        await registry.submit(turn(prompt="a"))
        await wait_until(lambda: runner.started == ["a"])
        assert registry.begin_delete("s1") is False
        runner.release("a")
        await wait_until(lambda: runner.finished == ["a"])
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_session_agent_reports_owner():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    runner.release("a")
    await registry.submit(Turn(session_id="s1", agent_id="agent-a", prompt="a"))
    assert registry.session_agent("s1") == "agent-a"
    assert registry.session_agent("missing") is None
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


@pytest.mark.asyncio
async def test_begin_agent_delete_blocks_its_turns_until_finished():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    try:
        assert registry.begin_agent_delete("agent-a") is True
        with pytest.raises(AgentDeletingError):
            await registry.submit(
                Turn(session_id="s-a", agent_id="agent-a", prompt="blocked")
            )
        # Other Agents are unaffected.
        runner.release("other")
        await registry.submit(
            Turn(session_id="s-b", agent_id="agent-b", prompt="other")
        )
        await wait_until(lambda: runner.finished == ["other"])

        registry.finish_agent_delete("agent-a")
        runner.release("after")
        await registry.submit(
            Turn(session_id="s-a", agent_id="agent-a", prompt="after")
        )
        await wait_until(lambda: runner.finished == ["other", "after"])
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_begin_agent_delete_refuses_a_busy_agent():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    try:
        await registry.submit(
            Turn(session_id="s-a", agent_id="agent-a", prompt="a")
        )
        await wait_until(lambda: runner.started == ["a"])
        assert registry.begin_agent_delete("agent-a") is False
        runner.release("a")
        await wait_until(lambda: runner.finished == ["a"])
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_close_agent_clients_has_a_total_budget(monkeypatch):
    from app.services import session_registry as registry_module

    class BlockingCloseRunner(FakeRunner):
        async def close(self, session_id):
            self.closed.append(session_id)
            await asyncio.Event().wait()

    runner = BlockingCloseRunner()
    registry = SessionRegistry(runner, concurrency=1)
    monkeypatch.setattr(registry_module, "_AGENT_CLOSE_BUDGET_SECONDS", 0.02)
    try:
        runner.release("a")
        await registry.submit(
            Turn(session_id="s-a", agent_id="agent-a", prompt="a")
        )
        await wait_until(lambda: runner.finished == ["a"])
        await wait_until(lambda: not registry.is_busy("s-a"))

        started = asyncio.get_running_loop().time()
        closed = await asyncio.wait_for(
            registry.close_agent_clients("agent-a"), 1
        )
        elapsed = asyncio.get_running_loop().time() - started
        assert closed == 0
        assert elapsed < 0.5
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_second_delete_marker_is_refused_until_the_first_finishes():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    try:
        assert registry.begin_delete("s1") is True
        assert registry.begin_delete("s1") is False
        registry.finish_delete("s1")
        assert registry.begin_delete("s1") is True
        registry.finish_delete("s1")

        assert registry.begin_agent_delete("agent-a") is True
        assert registry.begin_agent_delete("agent-a") is False
        registry.finish_agent_delete("agent-a")
        assert registry.begin_agent_delete("agent-a") is True
        registry.finish_agent_delete("agent-a")
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_removes_only_that_turn_and_preserves_order():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    first_id = await registry.submit(turn(prompt="a"))
    second_id = await registry.submit(turn(prompt="b"))
    third_id = await registry.submit(turn(prompt="c"))
    await wait_until(lambda: runner.started == ["a"])

    second_stream = registry.stream(second_id)
    assert await registry.cancel_queued("s1", second_id) is True
    assert second_stream.closed
    assert await drain_terminal(second_stream) == [
        {"type": "turn_end", "status": "cancelled"}
    ]

    runner.release("a")
    await wait_until(lambda: runner.started == ["a", "c"])
    runner.release("c")
    await wait_until(lambda: runner.finished == ["a", "c"])
    assert "b" not in runner.started
    assert registry.stream(first_id) is not None
    assert registry.stream(third_id) is not None
    await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_ignores_a_running_turn():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    running_id = await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])

    assert await registry.cancel_queued("s1", running_id) is False
    assert not registry.stream(running_id).closed
    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_cancels_turn_waiting_for_concurrency_permit():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    await registry.submit(turn(session_id="s1", prompt="a"))
    await wait_until(lambda: runner.started == ["a"])
    waiting_id = await registry.submit(turn(session_id="s2", prompt="b"))
    await wait_until(
        lambda: "s2" in registry._pending and "s2" not in registry._started
    )
    waiting_stream = registry.stream(waiting_id)

    assert await registry.cancel_queued("s2", waiting_id) is True
    assert waiting_stream.closed
    assert await drain_terminal(waiting_stream) == [
        {"type": "turn_end", "status": "cancelled"}
    ]

    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await wait_until(lambda: "s2" not in registry._workers)
    assert "b" not in runner.started
    await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_returns_false_for_unknown_turn():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])

    assert await registry.cancel_queued("s1", "missing") is False
    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_discards_a_queued_delivery():
    await init_db()
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db, channel_id="ch1", external_id="wx", session_id="s1"
        )
        await db.commit()
    queued = Turn(
        session_id="s1",
        agent_id="default",
        prompt="b",
        delivery=DeliveryRequest("ch1", "wx", delivery_id),
    )
    queued_id = await registry.submit(queued)
    try:
        assert await registry.cancel_queued("s1", queued_id) is True
        assert await channel_delivery.get(delivery_id) is None
    finally:
        runner.release("a")
        await wait_until(lambda: runner.finished == ["a"])
        await registry.close()
