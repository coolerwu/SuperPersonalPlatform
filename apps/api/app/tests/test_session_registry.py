import asyncio

import pytest

from app.services.claude_runner import Turn
from app.services.session_registry import SessionRegistry


class FakeRunner:
    def __init__(self):
        self.runs = []
        self.interrupted = []

    async def run(self, turn, stream):
        self.runs.append(turn.prompt)
        stream.publish({"type": "assistant_delta", "delta": turn.prompt})
        stream.close()

    async def interrupt(self, session_id):
        self.interrupted.append(session_id)
        return True


@pytest.mark.asyncio
async def test_turns_in_same_session_are_serialized():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    first = await registry.submit(Turn(session_id="s1", agent_id="default", prompt="a"))
    second = await registry.submit(Turn(session_id="s1", agent_id="default", prompt="b"))
    assert first != second
    assert registry.stream(first) is not registry.stream(second)
    await asyncio.sleep(0.1)
    await registry.close()
    assert runner.runs == ["a", "b"]


@pytest.mark.asyncio
async def test_interrupt_delegates():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    assert await registry.interrupt("s1") is True
    await registry.close()
    assert runner.interrupted == ["s1"]
