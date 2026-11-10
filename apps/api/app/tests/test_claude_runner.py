import asyncio
from types import SimpleNamespace

import pytest

from app.config import settings as app_settings
from app.db import init_db
from app.services import turn_lease
from app.services.claude_runner import (
    ClaudeTurnRunner,
    Turn,
    transcript_path,
)
from app.services.settings_store import AgentSection, PlatformSettings, ProviderSection
from app.services.turn_stream import TurnStream

SESSION_ID = "11111111-1111-1111-1111-111111111111"


class FakeClient:
    def __init__(self, messages=None, *, fail=False):
        self._messages = list(messages or [])
        self._fail = fail
        self.queries = []
        self.interrupted = False
        self.connected = False
        self.disconnected = False
        self.queried = asyncio.Event()

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def query(self, prompt):
        self.queries.append(prompt)
        self.queried.set()

    async def receive_messages(self):
        if self._fail:
            raise RuntimeError("stream exploded")
        for message in self._messages:
            yield message

    async def interrupt(self):
        self.interrupted = True


class HangingClient(FakeClient):
    async def receive_messages(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover - never reached


class StopsAfterResultClient(FakeClient):
    """Mimic a stream that ends right after its first result message."""

    async def receive_messages(self):
        for message in self._messages:
            yield message
            if type(message).__name__ == "ResultMessage":
                return


class FakeStore:
    def __init__(self, *agents):
        self._agents = [agent for agent in agents if agent is not None]

    async def get(self):
        return PlatformSettings(
            agents=self._agents, channels=[], channel_conversations={}
        )


class SlowStore(FakeStore):
    """Store whose first read blocks until the test releases it."""

    def __init__(self, *agents):
        super().__init__(*agents)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def get(self):
        self.entered.set()
        await self.release.wait()
        return await super().get()


class ResultMessage:
    def __init__(
        self,
        *,
        subtype="success",
        terminal_reason="completed",
        result="hello",
        usage=None,
        errors=None,
        api_error_status=None,
        is_error=False,
        origin=None,
    ):
        self.subtype = subtype
        self.terminal_reason = terminal_reason
        self.result = result
        self.usage = usage if usage is not None else {"input_tokens": 1, "output_tokens": 1}
        self.errors = errors
        self.api_error_status = api_error_status
        self.is_error = is_error
        self.origin = origin


def make_agent(agent_id="default", provider=None):
    return AgentSection(
        id=agent_id,
        name="Assistant",
        provider=provider
        or ProviderSection(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash[1m]",
            api_key="sk-test",
        ),
    )


def make_turn(session_id=SESSION_ID, agent_id="default", prompt="hi", source="manual"):
    return Turn(session_id=session_id, agent_id=agent_id, prompt=prompt, source=source)


async def drain_until_end(queue):
    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), 1)
        events.append(event)
        if event["type"] == "turn_end":
            return events


@pytest.mark.asyncio
async def test_runner_maps_stream_and_completes(tmp_path):
    await init_db()
    store = FakeStore(make_agent())
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
    turn = make_turn()
    outcome = await runner.run(turn, stream)
    assert outcome.status == "completed"
    assert outcome.output == "hello"
    assert client.queries == ["hi"]
    assert client.disconnected is False
    assert not await turn_lease.is_running(turn.session_id)


@pytest.mark.asyncio
async def test_runner_publishes_rich_turn_end(tmp_path):
    await init_db()
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    stream = TurnStream()
    queue = await stream.subscribe()
    task = asyncio.create_task(
        runner.run(make_turn(session_id="22222222-2222-2222-2222-222222222222"), stream)
    )
    await task
    end = (await drain_until_end(queue))[-1]
    assert end["type"] == "turn_end"
    assert end["status"] == "completed"
    assert end["error"] is None
    assert end["usage"] == {"input_tokens": 1, "output_tokens": 1}


@pytest.mark.asyncio
async def test_agent_not_found_ends_with_failed_turn_end(tmp_path):
    await init_db()

    def unused_factory(options):
        raise AssertionError("client must not be created")

    runner = ClaudeTurnRunner(
        store=FakeStore(),
        home=tmp_path,
        client_factory=unused_factory,
    )
    stream = TurnStream()
    queue = await stream.subscribe()
    turn = make_turn(session_id="33333333-3333-3333-3333-333333333333")
    task = asyncio.create_task(runner.run(turn, stream))
    outcome = await task
    events = await drain_until_end(queue)
    assert outcome.status == "failed"
    assert outcome.error == "agent not found"
    assert events[-1]["type"] == "turn_end"
    assert events[-1]["status"] == "failed"
    assert events[-1]["error"] == "agent not found"
    assert not any(event["type"] == "turn_error" for event in events)
    assert not await turn_lease.is_running(turn.session_id)


@pytest.mark.asyncio
async def test_missing_provider_fields_fail_and_release_lease(tmp_path):
    await init_db()
    agent = make_agent(provider=ProviderSection())

    def unused_factory(options):
        raise AssertionError("client must not be created")

    runner = ClaudeTurnRunner(
        store=FakeStore(agent),
        home=tmp_path,
        client_factory=unused_factory,
    )
    turn = make_turn(session_id="44444444-4444-4444-4444-444444444444")
    outcome = await runner.run(turn, TurnStream())
    assert outcome.status == "failed"
    assert "not configured" in outcome.error
    assert not await turn_lease.is_running(turn.session_id)


@pytest.mark.asyncio
async def test_timeout_fails_evicts_and_removes_lease(tmp_path, monkeypatch):
    await init_db()
    monkeypatch.setattr(app_settings, "run_timeout_seconds", 0.05)
    client = HangingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="55555555-5555-5555-5555-555555555555")
    outcome = await runner.run(turn, TurnStream())
    assert outcome.status == "failed"
    assert "timeout" in outcome.error
    assert client.interrupted is False
    assert client.disconnected is True
    assert not await turn_lease.is_running(turn.session_id)


@pytest.mark.asyncio
async def test_only_human_origin_result_completes_turn(tmp_path):
    await init_db()
    messages = [
        ResultMessage(result="injected", origin={"kind": "task-notification"}),
        ResultMessage(result="hello", origin={"kind": "human"}),
    ]
    client = FakeClient(messages)
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="66666666-6666-6666-6666-666666666666")
    outcome = await runner.run(turn, TurnStream())
    assert outcome.status == "completed"
    assert outcome.output == "hello"
    assert client.disconnected is False


@pytest.mark.asyncio
async def test_stream_ending_after_first_result_evicts_client(tmp_path):
    await init_db()
    first = StopsAfterResultClient(
        [
            ResultMessage(result="injected", origin={"kind": "task-notification"}),
            ResultMessage(result="hello"),
        ]
    )
    second = FakeClient([ResultMessage(result="hello")])
    clients = [first, second]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: clients.pop(0),
    )
    turn = make_turn(session_id="77777777-7777-7777-7777-777777777777")
    failed = await runner.run(turn, TurnStream())
    assert failed.status == "failed"
    assert failed.error == "run ended without result"
    assert first.disconnected is True

    completed = await runner.run(turn, TurnStream())
    assert completed.status == "completed"
    assert completed.output == "hello"
    assert second.queries == ["hi"]
    assert clients == []


@pytest.mark.asyncio
async def test_stream_ending_without_result_fails_and_evicts(tmp_path):
    await init_db()
    client = FakeClient([])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="88888888-8888-8888-8888-888888888888")
    outcome = await runner.run(turn, TurnStream())
    assert outcome.status == "failed"
    assert outcome.error == "run ended without result"
    assert client.disconnected is True
    assert not await turn_lease.is_running(turn.session_id)


@pytest.mark.asyncio
async def test_new_session_sets_session_id_until_transcript_exists(tmp_path):
    await init_db()
    captured = []
    client = FakeClient([ResultMessage()])

    def factory(options):
        captured.append(options)
        return client

    agent = make_agent()
    turn = make_turn(session_id="99999999-9999-9999-9999-999999999999")
    runner = ClaudeTurnRunner(store=FakeStore(agent), home=tmp_path, client_factory=factory)
    await runner.run(turn, TurnStream())
    assert captured[0].session_id == turn.session_id
    assert captured[0].resume is None

    path = transcript_path(turn.session_id, agent.id, home=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    captured.clear()
    runner = ClaudeTurnRunner(
        store=FakeStore(agent), home=tmp_path, client_factory=factory
    )
    await runner.run(turn, TurnStream())
    assert captured[0].resume == turn.session_id
    assert captured[0].session_id is None


@pytest.mark.asyncio
async def test_failure_evicts_client_for_next_turn(tmp_path):
    await init_db()
    first = FakeClient(fail=True)
    second = FakeClient([ResultMessage()])
    clients = [first, second]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: clients.pop(0),
    )
    turn = make_turn(session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    failed = await runner.run(turn, TurnStream())
    assert failed.status == "failed"
    assert first.disconnected is True

    completed = await runner.run(turn, TurnStream())
    assert completed.status == "completed"
    assert second.queries == ["hi"]
    assert clients == []


@pytest.mark.asyncio
async def test_failed_result_evicts_client_for_next_turn(tmp_path):
    await init_db()
    first = FakeClient(
        [
            ResultMessage(
                subtype="error",
                terminal_reason="error",
                result="boom",
                is_error=True,
            )
        ]
    )
    second = FakeClient([ResultMessage(result="hello")])
    clients = [first, second]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: clients.pop(0),
    )
    turn = make_turn(session_id="cccccccc-cccc-cccc-cccc-cccccccccccc")
    failed = await runner.run(turn, TurnStream())
    assert failed.status == "failed"
    assert failed.error == "boom"
    assert first.disconnected is True

    completed = await runner.run(turn, TurnStream())
    assert completed.status == "completed"
    assert second.queries == ["hi"]
    assert clients == []


@pytest.mark.asyncio
async def test_close_all_disconnects_cached_clients(tmp_path):
    await init_db()
    first = FakeClient([ResultMessage()])
    second = FakeClient([ResultMessage()])
    clients = [first, second]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: clients.pop(0),
    )
    await runner.run(
        make_turn(session_id="dddddddd-dddd-dddd-dddd-dddddddddddd"), TurnStream()
    )
    await runner.run(
        make_turn(session_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"), TurnStream()
    )
    assert len(runner._clients) == 2

    await runner.close_all()
    assert first.disconnected is True
    assert second.disconnected is True
    assert runner._clients == {}


@pytest.mark.asyncio
async def test_cancellation_evicts_and_publishes_cancelled_end(tmp_path):
    await init_db()
    client = HangingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    stream = TurnStream()
    queue = await stream.subscribe()
    task = asyncio.create_task(runner.run(turn, stream))
    await asyncio.wait_for(client.queried.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    end = (await drain_until_end(queue))[-1]
    assert end["type"] == "turn_end"
    assert end["status"] == "cancelled"
    assert client.disconnected is True
    assert not await turn_lease.is_running(turn.session_id)


@pytest.mark.asyncio
async def test_interrupt_ignores_idle_cached_client(tmp_path):
    await init_db()
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="99999999-9999-9999-9999-999999999999")
    await runner.run(turn, TurnStream())

    assert turn.session_id in runner._clients
    assert await runner.interrupt(turn.session_id) is False
    assert client.interrupted is False


@pytest.mark.asyncio
async def test_interrupt_reaches_client_only_while_turn_is_active(tmp_path):
    await init_db()
    client = HangingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="aaaaaaaa-1111-1111-1111-111111111111")
    task = asyncio.create_task(runner.run(turn, TurnStream()))
    await asyncio.wait_for(client.queried.wait(), 1)

    assert await runner.interrupt(turn.session_id) is True
    assert client.interrupted is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await turn_lease.is_running(turn.session_id)
    assert runner._active == set()


@pytest.mark.asyncio
async def test_interrupt_during_store_wait_cancels_before_query(tmp_path):
    await init_db()
    client = FakeClient([ResultMessage()])
    store = SlowStore(make_agent())
    runner = ClaudeTurnRunner(
        store=store,
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="aaaaaaaa-2222-2222-2222-222222222222")
    task = asyncio.create_task(runner.run(turn, TurnStream()))
    await asyncio.wait_for(store.entered.wait(), 1)

    assert await runner.interrupt(turn.session_id) is True
    store.release.set()
    outcome = await asyncio.wait_for(task, 1)

    assert outcome.status == "cancelled"
    assert client.queries == []
    assert runner._active == set()
    assert runner._cancel_requested == set()
    assert not await turn_lease.is_running(turn.session_id)
