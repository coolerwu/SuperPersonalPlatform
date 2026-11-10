import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import init_db
from app.services import turn_lease
from app.services.claude_runner import ClaudeTurnRunner, Turn
from app.services.settings_store import AgentSection, PlatformSettings, ProviderSection
from app.services.turn_stream import TurnStream


class FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.queries = []
        self.interrupted = False

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        for message in self._messages:
            yield message

    async def interrupt(self):
        self.interrupted = True


class FakeStore:
    def __init__(self, agent):
        self._agent = agent

    async def get(self):
        return PlatformSettings(agents=[self._agent], channels=[], channel_conversations={})


class ResultMessage:
    subtype = "success"
    terminal_reason = "completed"
    result = "hello"
    usage = {"input_tokens": 1, "output_tokens": 1}
    errors = None
    api_error_status = None
    is_error = False


@pytest.mark.asyncio
async def test_runner_maps_stream_and_completes(tmp_path, monkeypatch):
    await init_db()
    agent = AgentSection(
        id="default",
        name="Assistant",
        provider=ProviderSection(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash[1m]",
            api_key="sk-test",
        ),
    )
    store = FakeStore(agent)
    messages = [
        SimpleNamespace(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            }
        ),
        ResultMessage(),
    ]
    client = FakeClient(messages)
    runner = ClaudeTurnRunner(
        store=store,
        home=tmp_path,
        client_factory=lambda options: client,
    )
    stream = TurnStream()
    turn = Turn(session_id="11111111-1111-1111-1111-111111111111", agent_id="default", prompt="hi")
    outcome = await runner.run(turn, stream)
    assert outcome.status == "completed"
    assert outcome.output == "hello"
    assert client.queries == ["hi"]
    assert not await turn_lease.is_running(turn.session_id)
