import asyncio

import pytest

from app.services.claude_runner import Turn
from app.services.session_registry import SessionRegistry


async def wait_until(predicate, timeout=2.0):
    async def _poll():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_poll(), timeout)


async def drain_terminal(stream):
    queue = await stream.subscribe()
    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), 1.0)
        events.append(event)
        if event.get("type") == "turn_end":
            return events


class FakeRunner:
    """Runner whose turns block on a per-prompt gate until the test releases it."""

    def __init__(self):
        self.started = []
        self.finished = []
        self.interrupted = []
        self.closed = []
        self.interrupt_result = False
        self.active = 0
        self.max_active = 0
        self._gates: dict[str, asyncio.Event] = {}

    def gate(self, prompt: str) -> asyncio.Event:
        return self._gates.setdefault(prompt, asyncio.Event())

    def release(self, prompt: str) -> None:
        self.gate(prompt).set()

    async def run(self, turn, stream):
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

    async def interrupt(self, session_id):
        self.interrupted.append(session_id)
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
    assert await registry.interrupt("idle") is False
    await registry.close()
    assert runner.interrupted == ["idle"]


@pytest.mark.asyncio
async def test_interrupt_delegates_to_active_runner():
    runner = FakeRunner()
    runner.interrupt_result = True
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])

    assert await registry.interrupt("s1") is True
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
    assert await registry.interrupt("s1") is True
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

    assert await registry.interrupt("s1") is True
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

    assert await registry.interrupt("s2") is True
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
