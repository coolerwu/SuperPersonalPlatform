import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import settings as app_settings
from app.db import init_db
from app.prompts import DEFAULT_AGENT_INSTRUCTIONS
from app.services import claude_runner, turn_lease
from app.services.claude_runner import (
    ClaudeTurnRunner,
    Turn,
    _CachedClient,
    transcript_path,
)
from app.services.settings_store import (
    AgentSection,
    MapsSection,
    PlatformSettings,
    ProviderSection,
)
from app.services.turn_stream import TurnStream

SESSION_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
async def _cancel_client_sweepers():
    """Do not leave background sweep/tail tasks behind between tests."""
    yield
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is current or not task.get_name().startswith(
            ("claude-client-sweep", "session-task-tail-")
        ):
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


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


class OneShotClient(FakeClient):
    """Yield the scripted messages once; later reads see an empty stream.

    The runner starts a background tail reader after a completed turn, so a
    finite message list must not be replayed to it or every task event would
    be published twice.
    """

    def __init__(self, messages=None, *, fail=False):
        super().__init__(messages, fail=fail)
        self._consumed = False

    async def receive_messages(self):
        if self._consumed:
            return
        self._consumed = True
        async for message in super().receive_messages():
            yield message


class QueueClient(FakeClient):
    """Message stream fed by the test; each query enqueues one result.

    ``receive_messages`` stays open until disconnect, which is what a real CLI
    does while background tasks are still running.
    """

    def __init__(self):
        super().__init__()
        self.queue: asyncio.Queue = asyncio.Queue()

    async def receive_messages(self):
        while True:
            message = await self.queue.get()
            if message is None:
                return
            yield message

    async def query(self, prompt):
        self.queries.append(prompt)
        self.queried.set()
        await self.queue.put(ResultMessage())

    async def disconnect(self):
        self.disconnected = True
        await self.queue.put(None)


async def wait_for_published(tasks, count, timeout=2):
    """Wait until ``tasks.published`` has at least ``count`` entries."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while len(tasks.published) < count:
        if loop.time() >= deadline:
            raise AssertionError(
                f"timed out waiting for {count} published task event(s); "
                f"got {tasks.published!r}"
            )
        await asyncio.sleep(0.01)


class FakeStore:
    def __init__(self, *agents, maps=None):
        self._agents = [agent for agent in agents if agent is not None]
        self._maps = maps or MapsSection()

    async def get(self):
        return PlatformSettings(
            agents=self._agents,
            maps=self._maps,
            channels=[],
            channel_conversations={},
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
        _, event = await asyncio.wait_for(queue.get(), 1)
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
async def test_runner_records_slash_commands_from_init_frame(tmp_path):
    await init_db()
    init = SimpleNamespace(
        subtype="init",
        data={
            "slash_commands": ["compact", "/context", "exit", "model", "compact"],
            "terminal_slash_commands": ["exit", "model"],
        },
    )
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient([init, ResultMessage()]),
    )

    await runner.run(make_turn(), TurnStream())

    assert runner.slash_commands("default") == ["compact", "context"]
    assert runner.slash_commands("other") == []


@pytest.mark.asyncio
async def test_runner_records_runtime_init_snapshot(tmp_path):
    await init_db()
    init = SimpleNamespace(
        subtype="init",
        data={
            "slash_commands": ["compact", "exit"],
            "terminal_slash_commands": ["exit"],
            "tools": ["Read", "mcp__platform_tools__backtest_run"],
            "mcp_servers": [
                {"name": "platform_tools", "status": "connected", "source": "sdk"},
                {"name": "playwright", "status": "failed"},
            ],
            "plugins": [
                {"name": "playwright", "version": "1.0.0", "path": "/tmp/p"}
            ],
            "plugin_errors": [
                {
                    "plugin": "bad@market",
                    "type": "manifest-validation-error",
                    "message": "boom",
                    "path": "/tmp/b",
                }
            ],
            "skills": ["quant-backtest", "plugin:playwright"],
            "claude_code_version": "2.1.321",
            "model": "deepseek",
            "permissionMode": "bypassPermissions",
        },
    )
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient([init, ResultMessage()]),
    )

    await runner.run(make_turn(), TurnStream())

    snapshot = runner.runtime_snapshot("default")
    assert snapshot is not None
    assert snapshot["slash_commands"] == ["compact"]
    assert snapshot["tools"] == ["Read", "mcp__platform_tools__backtest_run"]
    assert snapshot["mcp_servers"] == [
        {"name": "platform_tools", "status": "connected", "source": "sdk"},
        {"name": "playwright", "status": "failed", "source": ""},
    ]
    assert snapshot["plugins"] == [{"name": "playwright", "version": "1.0.0"}]
    assert snapshot["plugin_errors"] == [
        {
            "plugin": "bad@market",
            "type": "manifest-validation-error",
            "message": "boom",
        }
    ]
    assert snapshot["skills"] == ["quant-backtest", "plugin:playwright"]
    assert snapshot["claude_code_version"] == "2.1.321"
    assert snapshot["model"] == "deepseek"
    assert snapshot["permission_mode"] == "bypassPermissions"
    assert runner.runtime_snapshot("other") is None


@pytest.mark.asyncio
async def test_runner_publishes_result_text_when_the_stream_had_none(tmp_path):
    await init_db()
    client = FakeClient([ResultMessage(result="compacted")])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    stream = TurnStream()
    queue = await stream.subscribe()

    await runner.run(make_turn(), stream)

    events = await drain_until_end(queue)
    assert events[0] == {"type": "assistant_delta", "delta": "compacted"}
    assert events[-1]["type"] == "turn_end"


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
async def test_close_all_disconnects_cached_clients_concurrently(
    tmp_path, monkeypatch
):
    await init_db()
    monkeypatch.setattr(claude_runner, "_DISCONNECT_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(claude_runner, "_SHUTDOWN_BUDGET_SECONDS", 0.5)

    started = 0
    all_started = asyncio.Event()

    class ConcurrentClient(FakeClient):
        async def disconnect(self):
            nonlocal started
            started += 1
            if started == 4:
                all_started.set()
            await all_started.wait()
            await super().disconnect()

    agent = make_agent()
    runner = ClaudeTurnRunner(store=FakeStore(agent), home=tmp_path)
    clients = [ConcurrentClient([ResultMessage()]) for _ in range(4)]
    for index, client in enumerate(clients):
        runner._clients[(agent.id, f"session-{index}")] = (
            claude_runner._CachedClient(
                client=client,
                agent_id=agent.id,
                fingerprint="",
                last_used=0.0,
            )
        )

    await asyncio.wait_for(runner.close_all(), 1)

    assert started == len(clients)
    assert all(client.disconnected for client in clients)
    assert runner._orphaned_clients == []


@pytest.mark.asyncio
async def test_disconnect_exception_retains_orphan(tmp_path):
    class ExplodingClient(FakeClient):
        async def disconnect(self):
            raise RuntimeError("boom")

    agent = make_agent()
    client = ExplodingClient([ResultMessage()])
    runner = ClaudeTurnRunner(store=FakeStore(agent), home=tmp_path)
    session_id = "dddddddd-1111-1111-1111-111111111111"
    runner._clients[(agent.id, session_id)] = claude_runner._CachedClient(
        client=client,
        agent_id=agent.id,
        fingerprint="",
        last_used=0.0,
    )

    await runner.close(session_id)

    assert runner._clients == {}
    assert runner._orphaned_clients == [client]


@pytest.mark.asyncio
async def test_close_all_exception_retains_orphan(tmp_path):
    class ExplodingClient(FakeClient):
        async def disconnect(self):
            raise RuntimeError("boom")

    agent = make_agent()
    client = ExplodingClient([ResultMessage()])
    runner = ClaudeTurnRunner(store=FakeStore(agent), home=tmp_path)
    runner._clients[(agent.id, "session")] = claude_runner._CachedClient(
        client=client,
        agent_id=agent.id,
        fingerprint="",
        last_used=0.0,
    )

    await runner.close_all()

    assert runner._clients == {}
    assert runner._orphaned_clients == [client]


@pytest.mark.asyncio
async def test_close_all_bounds_each_disconnect_and_keeps_orphans(
    tmp_path, monkeypatch, caplog
):
    await init_db()
    monkeypatch.setattr(claude_runner, "_DISCONNECT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(claude_runner, "_SHUTDOWN_BUDGET_SECONDS", 5)

    class SlowDisconnectClient(FakeClient):
        def __init__(self, messages=None, *, fail=False):
            super().__init__(messages, fail=fail)
            self.disconnect_started = asyncio.Event()

        async def disconnect(self):
            self.disconnect_started.set()
            await asyncio.Event().wait()

    first = SlowDisconnectClient([ResultMessage()])
    second = SlowDisconnectClient([ResultMessage()])
    clients = [first, second]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: clients.pop(0),
    )
    await runner.run(
        make_turn(session_id="dddddddd-ffff-ffff-ffff-ffffffffffff"), TurnStream()
    )
    await runner.run(
        make_turn(session_id="dddddddd-eeee-eeee-eeee-eeeeeeeeeeee"), TurnStream()
    )
    assert len(runner._clients) == 2

    # Each client gets its own bounded attempt; a wedged disconnect is kept
    # referenced instead of silently dropping the live CLI subprocess.
    await asyncio.wait_for(runner.close_all(), 1)
    assert first.disconnect_started.is_set()
    assert second.disconnect_started.is_set()
    assert runner._clients == {}
    assert {id(item) for item in runner._orphaned_clients} == {
        id(first),
        id(second),
    }
    assert any(
        "exceeded shutdown budget" in record.getMessage()
        for record in caplog.records
    )


def test_disconnect_budget_covers_the_sdk_close():
    # 20 s transport close + 5 s SDK MCP bridge grace + 5 s margin.
    assert claude_runner._DISCONNECT_TIMEOUT_SECONDS >= 30
    assert (
        claude_runner._SHUTDOWN_BUDGET_SECONDS
        > claude_runner._DISCONNECT_TIMEOUT_SECONDS
    )


def test_connect_timeout_exceeds_sdk_initialize():
    # The SDK's initialize timeout is max(env timeout, 60 s); let the SDK
    # report its own error before the platform's outer timeout fires.
    assert claude_runner._CONNECT_TIMEOUT_SECONDS >= 75


@pytest.mark.asyncio
async def test_shutdown_cancellation_keeps_lease_for_recovery(tmp_path):
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
    # registry.close() cancels the worker task during shutdown.
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        end = (await drain_until_end(queue))[-1]
        assert end["type"] == "turn_end"
        assert end["status"] == "cancelled"
        assert client.disconnected is True
        # The lease stays behind so recover_running_sessions can resume it
        # after the process restarts.
        assert await turn_lease.is_running(turn.session_id) is True
    finally:
        await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_cli_signal_exit_keeps_lease_for_recovery(tmp_path):
    await init_db()

    class SignalExitClient(FakeClient):
        async def receive_messages(self):
            from claude_agent_sdk._errors import ProcessError

            raise ProcessError("Command failed with exit code 143 (exit code: 143)")
            yield  # pragma: no cover - never reached

    client = SignalExitClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="cccccccc-cccc-cccc-cccc-cccccccccccc")
    stream = TurnStream()
    queue = await stream.subscribe()
    await runner.run(turn, stream)
    end = (await drain_until_end(queue))[-1]
    assert end["type"] == "turn_end"
    assert end["status"] == "cancelled"
    # systemd SIGTERM killed the CLI before the app could cancel the turn;
    # keep the lease so startup recovery resumes the session.
    assert await turn_lease.is_running(turn.session_id) is True
    await turn_lease.remove(turn.session_id)


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

    assert (turn.agent_id, turn.session_id) in runner._clients
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

    # Hard cancellation is shutdown semantics: keep the lease for recovery.
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await turn_lease.is_running(turn.session_id) is True
        assert runner._active == set()
    finally:
        await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_interrupt_with_a_stale_turn_id_is_ignored(tmp_path):
    await init_db()
    client = HangingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="abababab-abab-abab-abab-abababababab")
    stream = TurnStream()
    task = asyncio.create_task(runner.run(turn, stream))
    await asyncio.wait_for(client.queried.wait(), 1)
    try:
        assert (
            await runner.interrupt(
                turn.session_id, turn.agent_id, turn_id="stale-turn"
            )
            is False
        )
        assert client.interrupted is False
        assert (
            await runner.interrupt(
                turn.session_id, turn.agent_id, turn_id=turn.id
            )
            is True
        )
        assert client.interrupted is True
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_interrupt_waits_for_query_before_sending_control_request(
    tmp_path,
):
    await init_db()
    release = asyncio.Event()

    class RacingClient(FakeClient):
        def __init__(self):
            super().__init__([ResultMessage()])
            self.events: list[str] = []
            self.query_started = asyncio.Event()

        async def query(self, prompt):
            self.events.append("query-start")
            self.query_started.set()
            await release.wait()
            self.events.append("query-end")

        async def interrupt(self):
            self.events.append("interrupt")
            self.interrupted = True

    client = RacingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="aaaaaaaa-3333-3333-3333-333333333333")
    task = asyncio.create_task(runner.run(turn, TurnStream()))
    try:
        await asyncio.wait_for(client.query_started.wait(), 1)

        assert await runner.interrupt(turn.session_id) is True
        assert client.events == ["query-start"]

        release.set()
        await asyncio.wait_for(task, 1)
        assert client.events == ["query-start", "query-end", "interrupt"]
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_interrupt_returns_false_when_control_request_fails(tmp_path):
    class FailingInterruptClient(HangingClient):
        async def interrupt(self):
            raise RuntimeError("control channel closed")

    await init_db()
    client = FailingInterruptClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="aaaaaaaa-4444-4444-4444-444444444444")
    task = asyncio.create_task(runner.run(turn, TurnStream()))
    try:
        await asyncio.wait_for(client.queried.wait(), 1)
        await asyncio.sleep(0)

        assert await runner.interrupt(turn.session_id) is False
        assert (
            turn.agent_id,
            turn.session_id,
        ) not in runner._cancel_requested
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_interrupt_after_query_failure_lets_turn_finish(tmp_path):
    release_query = asyncio.Event()

    class RacingFailingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.query_started = asyncio.Event()
            self.receive_started = asyncio.Event()
            self.interrupt_attempts = 0

        async def query(self, prompt):
            self.query_started.set()
            await release_query.wait()

        async def interrupt(self):
            self.interrupt_attempts += 1
            raise RuntimeError("control channel closed")

        async def receive_messages(self):
            self.receive_started.set()
            await asyncio.Event().wait()
            yield  # pragma: no cover - never reached

    await init_db()
    client = RacingFailingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="aaaaaaaa-5555-5555-5555-555555555555")
    task = asyncio.create_task(runner.run(turn, TurnStream()))
    try:
        await asyncio.wait_for(client.query_started.wait(), 1)
        assert await runner.interrupt(turn.session_id) is True
        release_query.set()
        await asyncio.wait_for(client.receive_started.wait(), 1)

        assert client.interrupt_attempts == 1
        assert (
            turn.agent_id,
            turn.session_id,
        ) not in runner._cancel_requested
    finally:
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_after_turn_keeps_pending_eviction_when_cancelled(tmp_path, monkeypatch):
    runner = ClaudeTurnRunner(store=FakeStore(make_agent()), home=tmp_path)
    key = ("default", "session")

    async def cancelled_evict(*args, **kwargs):
        raise asyncio.CancelledError()

    runner._evict_pending.add(key)
    monkeypatch.setattr(runner, "_evict_key", cancelled_evict)
    with pytest.raises(asyncio.CancelledError):
        await runner.after_turn("session", "default")
    assert key in runner._evict_pending


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
    assert client.connected is False
    assert runner._active == set()
    assert runner._cancel_requested == set()
    assert not await turn_lease.is_running(turn.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instructions",
    [DEFAULT_AGENT_INSTRUCTIONS, "自定义指令：只回答中文。"],
)
async def test_agent_instructions_append_to_preset_system_prompt(
    tmp_path, instructions
):
    await init_db()
    captured = []
    client = FakeClient([ResultMessage()])

    def factory(options):
        captured.append(options)
        return client

    agent = make_agent()
    agent.instructions = instructions
    runner = ClaudeTurnRunner(
        store=FakeStore(agent), home=tmp_path, client_factory=factory
    )
    await runner.run(make_turn(), TurnStream())

    assert captured[0].system_prompt == {
        "type": "preset",
        "preset": "claude_code",
        "append": instructions,
    }


@pytest.mark.asyncio
async def test_empty_instructions_keep_preset_without_append(tmp_path):
    await init_db()
    captured = []
    client = FakeClient([ResultMessage()])
    agent = make_agent()
    assert agent.instructions == ""

    def factory(options):
        captured.append(options)
        return client

    runner = ClaudeTurnRunner(
        store=FakeStore(agent), home=tmp_path, client_factory=factory
    )
    await runner.run(make_turn(), TurnStream())

    assert captured[0].system_prompt == {
        "type": "preset",
        "preset": "claude_code",
    }
    assert "append" not in captured[0].system_prompt


async def _wait_until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_invalid_agent_id_fails_before_creating_workspace(tmp_path):
    await init_db()
    agent = AgentSection(
        id="../escape",
        name="Bad",
        provider=ProviderSection(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash[1m]",
            api_key="sk-test",
        ),
    )
    runner = ClaudeTurnRunner(
        store=FakeStore(agent),
        home=tmp_path,
        client_factory=lambda options: FakeClient([ResultMessage()]),
    )
    turn = Turn(session_id=SESSION_ID, agent_id="../escape", prompt="hi")

    outcome = await runner.run(turn, TurnStream())

    assert outcome.status == "failed"
    assert "invalid agent id" in (outcome.error or "")
    assert not (tmp_path / "workspaces" / ".." / "escape").exists()
    assert not await turn_lease.is_running(turn.session_id)


@pytest.mark.asyncio
async def test_agent_settings_change_closes_idle_client_and_rebuilds(tmp_path):
    from app.services.session_registry import SessionRegistry

    await init_db()
    captured = []
    clients = [FakeClient([ResultMessage()]), FakeClient([ResultMessage()])]

    def factory(options):
        captured.append(options)
        return clients[len(captured) - 1]

    agent = make_agent()
    agent.instructions = "旧指令"
    store = FakeStore(agent)
    runner = ClaudeTurnRunner(store=store, home=tmp_path, client_factory=factory)
    registry = SessionRegistry(runner, concurrency=1)
    turn = make_turn()

    try:
        await registry.submit(turn)
        await _wait_until(lambda: len(captured) == 1 and not registry.is_busy(turn.session_id))
        assert captured[0].system_prompt["append"] == "旧指令"

        store._agents[0].instructions = "新指令"
        assert await registry.close_agent_clients("default") == 1
        assert clients[0].disconnected is True

        await registry.submit(make_turn())
        await _wait_until(lambda: len(captured) == 2 and not registry.is_busy(turn.session_id))
        assert captured[1].system_prompt["append"] == "新指令"
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_options_raise_frame_buffer_and_wire_stderr_logging(tmp_path):
    await init_db()
    captured = []
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: captured.append(options) or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())
    assert captured[0].max_buffer_size == 16 * 1024 * 1024
    assert captured[0].include_partial_messages is True
    assert captured[0].forward_subagent_text is True
    assert callable(captured[0].stderr)
    servers = captured[0].mcp_servers
    assert servers["platform_tools"]["type"] == "sdk"
    assert servers["platform_tools"]["name"] == "platform_tools"
    assert "mcp__platform_tools__backtest_run" in captured[0].allowed_tools


@pytest.mark.asyncio
async def test_restricted_agent_uses_dont_ask_and_workspace_guard(tmp_path):
    await init_db()
    captured = []
    agent = make_agent()
    agent.access = "workspace"
    runner = ClaudeTurnRunner(
        store=FakeStore(agent),
        home=tmp_path,
        client_factory=lambda options: captured.append(options)
        or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())

    options = captured[0]
    assert options.permission_mode == "dontAsk"
    for name in ("Read", "Bash", "Task", "WebSearch", "mcp__platform_tools__backtest_run"):
        assert name in options.allowed_tools
    matchers = options.hooks["PreToolUse"]
    assert matchers[0].on_failure == "block"
    guard = matchers[0].hooks[0]
    decision = await guard(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/etc/passwd"},
            "tool_use_id": "tool-1",
        },
        "tool-1",
        {},
    )
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_full_access_agent_keeps_bypass_and_no_hooks(tmp_path):
    await init_db()
    captured = []
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: captured.append(options)
        or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())

    assert captured[0].permission_mode == "bypassPermissions"
    assert not captured[0].hooks


def test_config_fingerprint_includes_access():
    agent = make_agent()
    full = claude_runner._config_fingerprint(agent)
    agent.access = "workspace"
    restricted = claude_runner._config_fingerprint(agent)
    assert full != restricted


@pytest.mark.asyncio
async def test_idle_client_is_evicted_after_ttl(tmp_path):
    await init_db()
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        client_ttl_seconds=0.01,
        sweep_seconds=3600,
    )
    turn = make_turn(session_id="12121212-1212-1212-1212-121212121212")
    await runner.run(turn, TurnStream())
    assert (turn.agent_id, turn.session_id) in runner._clients

    await asyncio.sleep(0.02)
    assert await runner.sweep_idle_clients() == 1
    assert runner._clients == {}
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_sweep_never_evicts_a_running_client(tmp_path):
    await init_db()
    client = HangingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        client_ttl_seconds=0,
        sweep_seconds=3600,
    )
    turn = make_turn(session_id="13131313-1313-1313-1313-131313131313")
    stream = TurnStream()
    task = asyncio.create_task(runner.run(turn, stream))
    try:
        await asyncio.wait_for(client.queried.wait(), 1)
        assert await runner.sweep_idle_clients() == 0
        assert (turn.agent_id, turn.session_id) in runner._clients
        assert client.disconnected is False
    finally:
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_capacity_evicts_the_oldest_idle_client(tmp_path):
    await init_db()
    first = FakeClient([ResultMessage()])
    second = FakeClient([ResultMessage()])
    clients = [first, second]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: clients.pop(0),
        max_clients=1,
        client_ttl_seconds=3600,
        sweep_seconds=3600,
    )
    first_turn = make_turn(session_id="14141414-1414-1414-1414-141414141414")
    second_turn = make_turn(session_id="15151515-1515-1515-1515-151515151515")
    await runner.run(first_turn, TurnStream())
    # Make the LRU order deterministic instead of relying on clock resolution.
    runner._clients[(first_turn.agent_id, first_turn.session_id)].last_used -= 10
    await runner.run(second_turn, TurnStream())

    assert first.disconnected is True
    assert [key[1] for key in runner._clients] == [second_turn.session_id]


@pytest.mark.asyncio
async def test_changed_instructions_rebuild_a_cached_client(tmp_path):
    await init_db()
    captured = []
    clients = [FakeClient([ResultMessage()]), FakeClient([ResultMessage()])]
    tasks = FakeTaskService()

    def factory(options):
        captured.append(options)
        return clients[len(captured) - 1]

    agent = make_agent()
    agent.instructions = "旧指令"
    runner = ClaudeTurnRunner(
        store=FakeStore(agent),
        home=tmp_path,
        client_factory=factory,
        session_tasks=tasks,
    )
    turn = make_turn(session_id="16161616-1616-1616-1616-161616161616")
    await runner.run(turn, TurnStream())
    agent.instructions = "新指令"
    await runner.run(turn, TurnStream())

    assert len(captured) == 2
    assert clients[0].disconnected is True
    assert captured[1].system_prompt["append"] == "新指令"
    assert tasks.interrupted == [(turn.session_id, "SDK client 已断开")]


@pytest.mark.asyncio
async def test_same_session_id_across_agents_uses_separate_clients(tmp_path):
    await init_db()
    captured = []
    clients = [FakeClient([ResultMessage()]), FakeClient([ResultMessage()])]

    def factory(options):
        captured.append(options)
        return clients[len(captured) - 1]

    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent("agent-a"), make_agent("agent-b")),
        home=tmp_path,
        client_factory=factory,
    )
    session_id = "17171717-1717-1717-1717-171717171717"
    await runner.run(make_turn(session_id=session_id, agent_id="agent-a"), TurnStream())
    await runner.run(make_turn(session_id=session_id, agent_id="agent-b"), TurnStream())

    assert len(captured) == 2
    assert captured[0].cwd != captured[1].cwd
    assert set(runner._clients) == {
        ("agent-a", session_id),
        ("agent-b", session_id),
    }


@pytest.mark.asyncio
async def test_connect_timeout_fails_the_turn_and_disconnects(tmp_path):
    await init_db()

    class HangingConnectClient(FakeClient):
        async def connect(self):
            await asyncio.Event().wait()

    client = HangingConnectClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        connect_timeout_seconds=0.01,
    )
    outcome = await runner.run(
        make_turn(session_id="18181818-1818-1818-1818-181818181818"),
        TurnStream(),
    )
    assert outcome.status == "failed"
    assert "connect timed out" in (outcome.error or "")
    assert client.disconnected is True
    assert runner._clients == {}


@pytest.mark.asyncio
async def test_deferred_eviction_waits_for_after_turn(tmp_path):
    await init_db()
    client = FakeClient(fail=True)
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="19191919-1919-1919-1919-191919191919")
    outcome = await runner.run(turn, TurnStream(), defer_eviction=True)

    assert outcome.status == "failed"
    assert client.disconnected is False
    assert (turn.agent_id, turn.session_id) in runner._evict_pending

    await runner.after_turn(turn.session_id, turn.agent_id)
    assert client.disconnected is True
    assert runner._evict_pending == set()


@pytest.mark.asyncio
async def test_slow_eviction_does_not_block_the_next_turn(tmp_path, monkeypatch):
    from app.services.session_registry import SessionRegistry

    await init_db()
    monkeypatch.setattr(claude_runner, "_DISCONNECT_TIMEOUT_SECONDS", 0.02)
    evict_started = asyncio.Event()

    class SlowDisconnectClient(FakeClient):
        async def disconnect(self):
            evict_started.set()
            await asyncio.Event().wait()

    failing = SlowDisconnectClient(fail=True)
    succeeding = FakeClient([ResultMessage()])
    clients = [failing, succeeding]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: clients.pop(0),
    )
    registry = SessionRegistry(runner, concurrency=1)
    try:
        first = make_turn(session_id="20202020-2020-2020-2020-202020202020")
        second = make_turn(session_id="21212121-2121-2121-2121-212121212121")
        await registry.submit(first)
        await registry.submit(second)

        await asyncio.wait_for(evict_started.wait(), 1)
        # The second turn runs while the first client's disconnect is stuck.
        await _wait_until(lambda: succeeding.queries == ["hi"])
        assert registry.is_busy(first.session_id) is False
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_interrupt_marks_turn_active_under_timeout(tmp_path, monkeypatch):
    await init_db()
    client = HangingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="22222222-3333-3333-3333-333333333333")
    task = asyncio.create_task(runner.run(turn, TurnStream()))
    try:
        await asyncio.wait_for(client.queried.wait(), 1)
        assert await runner.interrupt(turn.session_id) is True
        assert client.interrupted is True
    finally:
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_interrupt_lock_timeout_still_marks_the_active_turn(tmp_path, monkeypatch):
    await init_db()
    monkeypatch.setattr(claude_runner, "_INTERRUPT_LOCK_TIMEOUT_SECONDS", 0.01)
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient([ResultMessage()]),
    )
    turn = make_turn(session_id="23232323-2323-2323-2323-232323232323")
    key = (turn.agent_id, turn.session_id)
    lock = runner._client_lock(turn.session_id)
    await lock.acquire()
    runner._active.add(key)
    try:
        assert await runner.interrupt(turn.session_id) is True
        assert key in runner._cancel_requested
    finally:
        runner._active.discard(key)
        runner._cancel_requested.discard(key)
        lock.release()


@pytest.mark.asyncio
async def test_idle_eviction_timeout_retains_an_orphan_reference(
    tmp_path, monkeypatch, caplog
):
    await init_db()
    monkeypatch.setattr(claude_runner, "_DISCONNECT_TIMEOUT_SECONDS", 0.01)

    class SlowDisconnectClient(FakeClient):
        async def disconnect(self):
            await asyncio.Event().wait()

    client = SlowDisconnectClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        client_ttl_seconds=0.01,
        sweep_seconds=3600,
    )
    turn = make_turn(session_id="24242424-2424-2424-2424-242424242424")
    await runner.run(turn, TurnStream())
    await asyncio.sleep(0.02)

    assert await runner.sweep_idle_clients() == 1
    assert runner._clients == {}
    assert runner._orphaned_clients == [client]
    assert any(
        "keeping reference" in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
async def test_sweep_skips_a_session_that_starts_during_another_disconnect(
    tmp_path, monkeypatch
):
    await init_db()
    monkeypatch.setattr(claude_runner, "_DISCONNECT_TIMEOUT_SECONDS", 1)
    release_first = asyncio.Event()
    first_disconnect_started = asyncio.Event()
    second_run_started = asyncio.Event()
    release_second = asyncio.Event()

    class GatedDisconnectClient(FakeClient):
        async def disconnect(self):
            first_disconnect_started.set()
            await release_first.wait()

    class SecondTurnHoldingClient(FakeClient):
        def __init__(self, messages=None, *, fail=False):
            super().__init__(messages, fail=fail)
            self.runs = 0

        async def receive_messages(self):
            self.runs += 1
            if self.runs >= 2:
                second_run_started.set()
                await release_second.wait()
            for message in self._messages:
                yield message

    first = GatedDisconnectClient([ResultMessage()])
    second = SecondTurnHoldingClient([ResultMessage()])
    clients = [first, second]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: clients.pop(0),
        client_ttl_seconds=0,
        sweep_seconds=3600,
    )
    first_turn = make_turn(session_id="25252525-2525-2525-2525-252525252525")
    second_turn = make_turn(session_id="26262626-2626-2626-2626-262626262626")
    await runner.run(first_turn, TurnStream())
    await runner.run(second_turn, TurnStream())

    sweep = asyncio.create_task(runner.sweep_idle_clients())
    try:
        await asyncio.wait_for(first_disconnect_started.wait(), 1)

        # The second session starts a turn while the first disconnect blocks.
        second_key = (second_turn.agent_id, second_turn.session_id)
        running = asyncio.create_task(runner.run(second_turn, TurnStream()))
        await asyncio.wait_for(second_run_started.wait(), 1)

        release_first.set()
        assert await asyncio.wait_for(sweep, 1) == 1
        # The stale sweep snapshot must not disconnect the now-active client.
        assert second_key in runner._clients
        assert second.disconnected is False

        release_second.set()
        outcome = await asyncio.wait_for(running, 1)
        assert outcome.status == "completed"
        assert second.disconnected is False
    finally:
        release_first.set()
        release_second.set()
        if not sweep.done():
            sweep.cancel()
        await runner.close_all()


@pytest.mark.asyncio
async def test_orphan_retry_releases_a_client_that_recovers(tmp_path, monkeypatch):
    await init_db()
    monkeypatch.setattr(claude_runner, "_DISCONNECT_TIMEOUT_SECONDS", 0.01)

    class RecoveringClient(FakeClient):
        def __init__(self, messages=None, *, fail=False):
            super().__init__(messages, fail=fail)
            self.disconnect_calls = 0

        async def disconnect(self):
            self.disconnect_calls += 1
            if self.disconnect_calls == 1:
                await asyncio.Event().wait()

    client = RecoveringClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        client_ttl_seconds=0.01,
        sweep_seconds=3600,
    )
    turn = make_turn(session_id="27272727-2727-2727-2727-272727272727")
    await runner.run(turn, TurnStream())
    await asyncio.sleep(0.02)

    assert await runner.sweep_idle_clients() == 1
    assert client.disconnect_calls == 2
    assert runner._orphaned_clients == []


@pytest.mark.asyncio
async def test_orphan_retry_keeps_client_after_failure(tmp_path, monkeypatch):
    await init_db()
    monkeypatch.setattr(claude_runner, "_DISCONNECT_TIMEOUT_SECONDS", 0.01)

    class FailingClient(FakeClient):
        async def disconnect(self):
            raise RuntimeError("boom")

    client = FailingClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        client_ttl_seconds=0.01,
        sweep_seconds=3600,
    )
    turn = make_turn(session_id="28282828-2828-2828-2828-282828282828")
    await runner.run(turn, TurnStream())
    await asyncio.sleep(0.02)

    assert await runner.sweep_idle_clients() == 1
    assert runner._orphaned_clients == [client]


def test_orphan_list_is_bounded():
    runner = ClaudeTurnRunner(store=FakeStore(make_agent()))
    for index in range(claude_runner._MAX_ORPHANED_CLIENTS + 5):
        runner._retain_orphan(object())
    assert len(runner._orphaned_clients) == claude_runner._MAX_ORPHANED_CLIENTS


@pytest.mark.asyncio
async def test_memory_profile_change_rebuilds_a_cached_client(tmp_path):
    await init_db()
    captured = []
    clients = [FakeClient([ResultMessage()]), FakeClient([ResultMessage()])]

    def factory(options):
        captured.append(options)
        return clients[len(captured) - 1]

    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()), home=tmp_path, client_factory=factory
    )
    turn = make_turn(session_id="28282828-2828-2828-2828-282828282828")
    await runner.run(turn, TurnStream())

    memory = tmp_path / "claude" / "CLAUDE.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("用户画像", "utf-8")

    await runner.run(turn, TurnStream())
    assert len(captured) == 2
    assert clients[0].disconnected is True


@pytest.mark.asyncio
async def test_plugin_change_rebuilds_a_cached_client(tmp_path):
    await init_db()
    captured = []
    clients = [FakeClient([ResultMessage()]), FakeClient([ResultMessage()])]

    def factory(options):
        captured.append(options)
        return clients[len(captured) - 1]

    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()), home=tmp_path, client_factory=factory
    )
    turn = make_turn(session_id="29292929-2929-2929-2929-292929292929")
    await runner.run(turn, TurnStream())

    revision = tmp_path / "claude" / ".plugins-revision"
    revision.parent.mkdir(parents=True, exist_ok=True)
    revision.write_text("changed", "utf-8")

    await runner.run(turn, TurnStream())
    assert len(captured) == 2
    assert clients[0].disconnected is True


@pytest.mark.asyncio
async def test_agent_memory_change_rebuilds_a_cached_client(tmp_path):
    await init_db()
    captured = []
    clients = [FakeClient([ResultMessage()]), FakeClient([ResultMessage()])]

    def factory(options):
        captured.append(options)
        return clients[len(captured) - 1]

    agent = make_agent()
    runner = ClaudeTurnRunner(
        store=FakeStore(agent), home=tmp_path, client_factory=factory
    )
    turn = make_turn(session_id="30303030-3030-3030-3030-303030303030")
    await runner.run(turn, TurnStream())

    memory = tmp_path / "workspaces" / agent.id / "CLAUDE.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("Agent memory", "utf-8")

    await runner.run(turn, TurnStream())
    assert len(captured) == 2
    assert clients[0].disconnected is True


def test_agent_memory_stamp_is_scoped_and_guarded(tmp_path):
    assert claude_runner._agent_memory_stamp("../escape", tmp_path) == ""
    first = tmp_path / "workspaces" / "agent-a" / "CLAUDE.md"
    second = tmp_path / "workspaces" / "agent-b" / "CLAUDE.md"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("memory", "utf-8")

    first_stamp = claude_runner._agent_memory_stamp("agent-a", tmp_path)
    second_stamp = claude_runner._agent_memory_stamp("agent-b", tmp_path)
    first.write_text("changed", "utf-8")
    assert claude_runner._agent_memory_stamp("agent-a", tmp_path) != first_stamp
    assert claude_runner._agent_memory_stamp("agent-b", tmp_path) == second_stamp


def test_config_fingerprint_includes_agent_memory():
    agent = make_agent()
    assert claude_runner._config_fingerprint(
        agent, "memory", "plugins", "agent-a"
    ) != claude_runner._config_fingerprint(
        agent, "memory", "plugins", "agent-b"
    )


@pytest.mark.asyncio
async def test_tool_call_events_carry_workspace_artifacts(tmp_path):
    await init_db()
    workspace = tmp_path / "workspaces" / "default"
    workspace.mkdir(parents=True)
    (workspace / "shot.png").write_bytes(b"png")
    messages = [
        SimpleNamespace(
            event={
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "mcp__playwright__browser_take_screenshot",
                },
            }
        ),
        SimpleNamespace(
            event={
                "type": "content_block_delta",
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"filename": "shot.png"}',
                },
            }
        ),
        SimpleNamespace(event={"type": "content_block_stop"}),
        ResultMessage(),
    ]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient(messages),
    )
    stream = TurnStream()
    queue = await stream.subscribe()
    await runner.run(make_turn(), stream)
    events = await drain_until_end(queue)
    tool_call = next(event for event in events if event["type"] == "tool_call")
    assert tool_call["name"] == "mcp__playwright__browser_take_screenshot"
    assert tool_call["artifact"] == {
        "path": "shot.png",
        "name": "shot.png",
        "size": 3,
        "kind": "image",
    }


@pytest.mark.asyncio
async def test_tool_call_events_without_files_have_no_artifact(tmp_path):
    await init_db()
    messages = [
        SimpleNamespace(
            event={
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Bash"},
            }
        ),
        SimpleNamespace(
            event={
                "type": "content_block_delta",
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"command": "ls"}',
                },
            }
        ),
        SimpleNamespace(event={"type": "content_block_stop"}),
        ResultMessage(),
    ]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient(messages),
    )
    stream = TurnStream()
    queue = await stream.subscribe()
    await runner.run(make_turn(), stream)
    events = await drain_until_end(queue)
    tool_call = next(event for event in events if event["type"] == "tool_call")
    assert "artifact" not in tool_call


class UserMessage:
    def __init__(self, content, origin=None):
        self.content = content
        self.origin = origin


class AssistantMessage:
    def __init__(self, content, parent_tool_use_id=None):
        self.content = content
        self.parent_tool_use_id = parent_tool_use_id


@pytest.mark.asyncio
async def test_runner_publishes_subagent_text_and_tools(tmp_path):
    await init_db()
    messages = [
        AssistantMessage(
            [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "child working"},
                {
                    "type": "tool_use",
                    "id": "s1",
                    "name": "Read",
                    "input": {"file_path": "a.txt"},
                },
            ],
            parent_tool_use_id="parent-1",
        ),
        ResultMessage(result="main"),
    ]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient(messages),
    )
    stream = TurnStream()
    queue = await stream.subscribe()

    outcome = await runner.run(make_turn(), stream)
    events = await drain_until_end(queue)

    text = next(event for event in events if event["type"] == "subagent_text")
    assert text["parent_tool_use_id"] == "parent-1"
    assert text["text"] == "child working"
    tool = next(event for event in events if event["type"] == "subagent_tool_call")
    assert tool["parent_tool_use_id"] == "parent-1"
    assert tool["id"] == "s1"
    assert tool["name"] == "Read"
    assert not any(event["type"] == "reasoning_delta" for event in events)
    assert outcome.output == "main"


@pytest.mark.asyncio
async def test_runner_publishes_task_lifecycle(tmp_path):
    await init_db()
    messages = [
        SimpleNamespace(
            subtype="task_started",
            task_id="task-1",
            description="background job",
            tool_use_id="tool-1",
            task_type="bash",
            data={},
        ),
        SimpleNamespace(
            subtype="task_progress",
            task_id="task-1",
            description="background job",
            tool_use_id="tool-1",
            last_tool_name="Bash",
            usage={"total_tokens": 3, "tool_uses": 1, "duration_ms": 2},
            data={},
        ),
        SimpleNamespace(
            subtype="task_notification",
            task_id="task-1",
            tool_use_id="tool-1",
            status="completed",
            summary="finished",
            usage=None,
            data={},
        ),
        ResultMessage(result="main"),
    ]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient(messages),
    )
    stream = TurnStream()
    queue = await stream.subscribe()

    await runner.run(make_turn(), stream)
    events = await drain_until_end(queue)

    started = next(event for event in events if event["type"] == "task_started")
    assert started["task_id"] == "task-1"
    assert started["description"] == "background job"
    progress = next(event for event in events if event["type"] == "task_progress")
    assert progress["last_tool_name"] == "Bash"
    notification = next(
        event for event in events if event["type"] == "task_notification"
    )
    assert notification["status"] == "completed"
    assert notification["summary"] == "finished"


@pytest.mark.asyncio
async def test_runner_suppresses_injected_turn_text(tmp_path):
    await init_db()
    messages = [
        UserMessage("notification", origin={"kind": "task-notification"}),
        SimpleNamespace(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "LEAK"},
            }
        ),
        ResultMessage(origin={"kind": "task-notification"}, result="notified"),
        SimpleNamespace(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "real"},
            }
        ),
        ResultMessage(result="real answer"),
    ]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient(messages),
    )
    stream = TurnStream()
    queue = await stream.subscribe()

    outcome = await runner.run(make_turn(), stream)
    events = await drain_until_end(queue)

    deltas = [
        event["delta"]
        for event in events
        if event["type"] == "assistant_delta"
    ]
    assert deltas == ["real"]
    assert outcome.output == "real answer"


@pytest.mark.asyncio
async def test_subagent_stream_events_are_not_duplicated_by_message(tmp_path):
    await init_db()
    messages = [
        SimpleNamespace(
            parent_tool_use_id="parent-1",
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "streamed"},
            },
        ),
        AssistantMessage(
            [{"type": "text", "text": "streamed"}],
            parent_tool_use_id="parent-1",
        ),
        ResultMessage(result="main"),
    ]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient(messages),
    )
    stream = TurnStream()
    queue = await stream.subscribe()

    await runner.run(make_turn(), stream)
    events = await drain_until_end(queue)

    texts = [event["text"] for event in events if event["type"] == "subagent_text"]
    assert texts == ["streamed"]


@pytest.mark.asyncio
async def test_options_include_amap_mcp_when_enabled(tmp_path):
    await init_db()
    captured = []
    store = FakeStore(
        make_agent(),
        maps=MapsSection(enabled=True, api_key="amap test&key"),
    )
    runner = ClaudeTurnRunner(
        store=store,
        home=tmp_path,
        client_factory=lambda options: captured.append(options)
        or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())
    servers = captured[0].mcp_servers
    assert servers["platform_tools"]["type"] == "sdk"
    assert servers["amap"] == {
        "type": "http",
        "url": "https://mcp.amap.com/mcp?key=amap%20test%26key",
    }
    assert "mcp__platform_tools__backtest_run" in captured[0].allowed_tools


@pytest.mark.asyncio
async def test_options_omit_amap_without_enable_or_key(tmp_path):
    await init_db()
    for maps in (
        MapsSection(enabled=False, api_key="amap-key"),
        MapsSection(enabled=True, api_key=""),
        MapsSection(enabled=True, api_key="   "),
    ):
        captured = []
        runner = ClaudeTurnRunner(
            store=FakeStore(make_agent(), maps=maps),
            home=tmp_path,
            client_factory=lambda options: captured.append(options)
            or FakeClient([ResultMessage()]),
        )
        await runner.run(make_turn(), TurnStream())
        assert "amap" not in captured[0].mcp_servers


@pytest.mark.asyncio
async def test_maps_config_change_rebuilds_client(tmp_path):
    await init_db()
    captured = []
    store = FakeStore(
        make_agent(), maps=MapsSection(enabled=True, api_key="key-a")
    )
    runner = ClaudeTurnRunner(
        store=store,
        home=tmp_path,
        client_factory=lambda options: captured.append(options)
        or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())
    assert captured[0].mcp_servers["amap"]["url"].endswith("key=key-a")

    store._maps = MapsSection(enabled=True, api_key="key-b")
    await runner.run(make_turn(), TurnStream())
    assert len(captured) == 2
    assert captured[1].mcp_servers["amap"]["url"].endswith("key=key-b")

    store._maps = MapsSection(enabled=False, api_key="key-b")
    await runner.run(make_turn(), TurnStream())
    assert len(captured) == 3
    assert "amap" not in captured[2].mcp_servers


@pytest.mark.asyncio
async def test_amap_url_trims_key(tmp_path):
    await init_db()
    captured = []
    runner = ClaudeTurnRunner(
        store=FakeStore(
            make_agent(),
            maps=MapsSection(enabled=True, api_key="  key-a  "),
        ),
        home=tmp_path,
        client_factory=lambda options: captured.append(options)
        or FakeClient([ResultMessage()]),
    )
    await runner.run(make_turn(), TurnStream())
    assert captured[0].mcp_servers["amap"]["url"].endswith("key=key-a")


def test_config_fingerprint_includes_maps():
    agent = make_agent()
    assert claude_runner._config_fingerprint(
        agent, "memory", "plugins", "agent-memory", "maps-a"
    ) != claude_runner._config_fingerprint(
        agent, "memory", "plugins", "agent-memory", "maps-b"
    )


def test_redact_cli_line_masks_amap_key():
    line = "connect https://mcp.amap.com/mcp?key=secret123&foo=1 failed"
    assert (
        claude_runner._redact_cli_line(line)
        == "connect https://mcp.amap.com/mcp?key=***&foo=1 failed"
    )


def test_redact_cli_line_masks_other_amap_url_forms():
    assert (
        claude_runner._redact_cli_line("mcp.amap.com/mcp?key=secret123 failed")
        == "mcp.amap.com/mcp?key=*** failed"
    )
    assert (
        claude_runner._redact_cli_line(
            '{"url": "https://MCP.AMAP.COM/mcp?key=secret123"}'
        )
        == '{"url": "https://mcp.amap.com/mcp?key=***"}'
    )


@pytest.mark.asyncio
async def test_result_error_redacts_amap_key(tmp_path):
    await init_db()
    client = FakeClient(
        [
            ResultMessage(
                subtype="error_during_execution",
                terminal_reason=None,
                result=None,
                errors=[
                    "connect failed: https://mcp.amap.com/mcp?key=secret123"
                ],
                is_error=True,
            )
        ]
    )
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    stream = TurnStream()
    queue = await stream.subscribe()
    turn = make_turn(session_id="12121212-1212-1212-1212-121212121212")
    outcome = await runner.run(turn, stream)
    end = (await drain_until_end(queue))[-1]
    assert outcome.status == "failed"
    assert end["type"] == "turn_end"
    assert "secret123" not in (end["error"] or "")
    assert "key=***" in (end["error"] or "")


class FakeTaskService:
    def __init__(self, *, keep_until=None):
        self.published = []
        self.interrupted = []
        self.flushed = []
        self.keep_until = keep_until

    async def publish(self, session_id, event):
        self.published.append((session_id, event))

    async def keep_alive_until(self, session_id):
        return self.keep_until

    async def mark_interrupted(self, session_id, reason):
        self.interrupted.append((session_id, reason))

    async def flush(self, session_id):
        self.flushed.append(session_id)


class TaskMessage:
    def __init__(self, task_id="t1"):
        self.subtype = "task_started"
        self.task_id = task_id
        self.tool_use_id = "toolu_1"
        self.description = "回测"

    def __getattr__(self, name):
        return None


class FailingFlushTaskService(FakeTaskService):
    async def flush(self, session_id):
        raise RuntimeError("flush exploded")


class FailingPublishTaskService(FakeTaskService):
    async def publish(self, session_id, event):
        raise RuntimeError("publish exploded")


class SlowDisconnectClient(FakeClient):
    async def disconnect(self):
        await asyncio.sleep(5)
        self.disconnected = True


class FailingDisconnectClient(FakeClient):
    async def disconnect(self):
        raise RuntimeError("disconnect exploded")


class OrderedDisconnectClient(FakeClient):
    """Record whether task interruption was marked before disconnecting."""

    def __init__(self, tasks, session_id):
        super().__init__([ResultMessage()])
        self.tasks = tasks
        self.session_id = session_id
        self.marked_before_disconnect: bool | None = None

    async def disconnect(self):
        self.marked_before_disconnect = any(
            sid == self.session_id for sid, _ in self.tasks.interrupted
        )
        await super().disconnect()


@pytest.mark.asyncio
async def test_task_events_publish_to_session_service(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    client = OneShotClient([TaskMessage(), ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="31313131-3131-3131-3131-313131313131"
    )

    await runner.run(turn, TurnStream())

    assert tasks.published == [(turn.session_id, {
        "type": "task_started",
        "task_id": "t1",
        "tool_use_id": "toolu_1",
        "description": "回测",
        "task_type": None,
    })]
    await runner.close(turn.session_id)


@pytest.mark.asyncio
async def test_tail_reader_publishes_task_events_after_turn(tmp_path):
    """F26: a task finishing after turn_end still reaches the session channel."""
    await init_db()
    tasks = FakeTaskService()
    client = QueueClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="51515151-5151-5151-5151-515151515151"
    )

    await runner.run(turn, TurnStream())

    assert tasks.published == []
    cached = runner._clients[("default", turn.session_id)]
    assert cached.tail_reader is not None

    # No second turn: the event arrives while the session is idle.
    await client.queue.put(TaskMessage())

    await wait_for_published(tasks, 1)
    assert tasks.published == [(turn.session_id, {
        "type": "task_started",
        "task_id": "t1",
        "tool_use_id": "toolu_1",
        "description": "回测",
        "task_type": None,
    })]
    await runner.close(turn.session_id)


@pytest.mark.asyncio
async def test_tail_reader_hands_stream_back_to_next_turn(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    client = QueueClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="52525252-5252-5252-5252-525252525252"
    )

    await runner.run(turn, TurnStream())
    await client.queue.put(TaskMessage())
    await wait_for_published(tasks, 1)
    assert len(tasks.published) == 1

    outcome = await runner.run(turn, TurnStream())

    assert outcome.status == "completed"
    # The event was published once by the tail reader, not replayed by the
    # second turn or by a duplicated reader.
    assert len(tasks.published) == 1
    cached = runner._clients[("default", turn.session_id)]
    assert cached.tail_reader is not None

    await client.queue.put(TaskMessage("t2"))
    await wait_for_published(tasks, 2)
    assert tasks.published[1][1]["task_id"] == "t2"
    await runner.close(turn.session_id)


@pytest.mark.asyncio
async def test_tail_reader_starts_before_client_leaves_active(tmp_path, monkeypatch):
    """The sweep must not see the client idle before its tail reader exists."""
    await init_db()
    tasks = FakeTaskService()
    client = QueueClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="54545454-5454-5454-5454-545454545454"
    )
    active_at_start = []
    start = runner._start_tail_reader

    def spy(key, cached):
        active_at_start.append(key in runner._active)
        start(key, cached)

    monkeypatch.setattr(runner, "_start_tail_reader", spy)

    await runner.run(turn, TurnStream())

    assert active_at_start == [True]
    await runner.close(turn.session_id)


@pytest.mark.asyncio
async def test_close_stops_tail_reader_and_drops_late_events(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    client = QueueClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="53535353-5353-5353-5353-535353535353"
    )

    await runner.run(turn, TurnStream())
    cached = runner._clients[("default", turn.session_id)]
    assert cached.tail_reader is not None

    await runner.close(turn.session_id)

    assert cached.tail_reader is None
    assert client.disconnected is True
    await client.queue.put(TaskMessage())
    await asyncio.sleep(0.05)
    assert tasks.published == []


@pytest.mark.asyncio
async def test_sweep_skips_client_with_active_background_task(tmp_path):
    await init_db()
    keep_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    tasks = FakeTaskService(keep_until=keep_until)
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        client_ttl_seconds=0,
        sweep_seconds=3600,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="32323232-3232-3232-3232-323232323232"
    )
    await runner.run(turn, TurnStream())
    await asyncio.sleep(0.01)

    assert await runner.sweep_idle_clients() == 0
    assert client.disconnected is False


@pytest.mark.asyncio
async def test_eviction_marks_background_tasks_interrupted(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="33333333-4444-4444-4444-444444444444"
    )
    await runner.run(turn, TurnStream())

    await runner.close(turn.session_id)

    assert tasks.flushed == [turn.session_id]
    assert tasks.interrupted == [(turn.session_id, "SDK client 已断开")]


@pytest.mark.asyncio
async def test_disconnect_marks_background_tasks_interrupted(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    session_id = "34343434-3434-3434-3434-343434343434"

    assert await runner._disconnect(client, session_id) is True

    assert tasks.interrupted == [(session_id, "SDK client 已断开")]


@pytest.mark.asyncio
async def test_disconnect_marks_tasks_before_disconnecting(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    session_id = "35353535-3535-3535-3535-353535353535"
    client = OrderedDisconnectClient(tasks, session_id)
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )

    assert await runner._disconnect(client, session_id) is True

    assert client.marked_before_disconnect is True


@pytest.mark.asyncio
async def test_disconnect_timeout_marks_background_tasks_interrupted(
    tmp_path, monkeypatch
):
    await init_db()
    monkeypatch.setattr(claude_runner, "_DISCONNECT_TIMEOUT_SECONDS", 0.01)
    tasks = FakeTaskService()
    session_id = "36363636-3636-3636-3636-363636363636"
    client = SlowDisconnectClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )

    assert await runner._disconnect(client, session_id) is False

    assert tasks.interrupted == [(session_id, "SDK client 已断开")]


@pytest.mark.asyncio
async def test_disconnect_failure_marks_background_tasks_interrupted(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    session_id = "37373737-3737-3737-3737-373737373737"
    client = FailingDisconnectClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )

    assert await runner._disconnect(client, session_id) is False

    assert tasks.interrupted == [(session_id, "SDK client 已断开")]


@pytest.mark.asyncio
async def test_flush_failure_still_ends_turn_lease(tmp_path):
    await init_db()
    tasks = FailingFlushTaskService()
    client = FakeClient([ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="38383838-3838-3838-3838-383838383838"
    )

    await runner.run(turn, TurnStream())

    assert await turn_lease.is_running(turn.session_id) is False


@pytest.mark.asyncio
async def test_task_publish_failure_does_not_break_turn(tmp_path):
    await init_db()
    tasks = FailingPublishTaskService()
    client = OneShotClient([TaskMessage(), ResultMessage()])
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
        session_tasks=tasks,
    )
    turn = make_turn(
        session_id="39393939-3939-3939-3939-393939393939"
    )

    outcome = await runner.run(turn, TurnStream())

    assert outcome.status == "completed"
    await runner.close(turn.session_id)


@pytest.mark.asyncio
async def test_capacity_evicts_oldest_pinned_client_after_grace(tmp_path):
    await init_db()
    keep_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    tasks = FakeTaskService(keep_until=keep_until)
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        max_clients=1,
        session_tasks=tasks,
    )
    session_ids = [
        "41414141-4141-4141-4141-414141414141",
        "42424242-4242-4242-4242-424242424242",
        "43434343-4343-4343-4343-434343434343",
        "44444444-4444-4444-4444-444444444444",
    ]
    for index, session_id in enumerate(session_ids):
        runner._clients[("default", session_id)] = _CachedClient(
            client=FakeClient([ResultMessage()]),
            agent_id="default",
            fingerprint="fingerprint",
            last_used=float(index),
        )

    await runner._enforce_capacity()

    assert len(runner._clients) == 3
    assert tasks.interrupted == [(session_ids[0], "SDK client 已断开")]


@pytest.mark.asyncio
async def test_shutdown_budget_exhausted_still_marks_tasks(tmp_path, monkeypatch):
    await init_db()
    monkeypatch.setattr(claude_runner, "_SHUTDOWN_BUDGET_SECONDS", 0)
    tasks = FakeTaskService()
    session_id = "46464646-4646-4646-4646-464646464646"
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        session_tasks=tasks,
    )
    runner._clients[("default", session_id)] = _CachedClient(
        client=FakeClient([ResultMessage()]),
        agent_id="default",
        fingerprint="fingerprint",
        last_used=0.0,
    )

    await runner.close_all()

    assert tasks.interrupted == [
        (session_id, "服务关机，SDK client 已断开")
    ]


@pytest.mark.asyncio
async def test_close_all_marks_each_client_once(tmp_path):
    await init_db()
    tasks = FakeTaskService()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        session_tasks=tasks,
    )
    session_ids = [
        "47474747-4747-4747-4747-474747474747",
        "48484848-4848-4848-4848-484848484848",
    ]
    for index, session_id in enumerate(session_ids):
        runner._clients[("default", session_id)] = _CachedClient(
            client=FakeClient([ResultMessage()]),
            agent_id="default",
            fingerprint="fingerprint",
            last_used=float(index),
        )

    await runner.close_all()

    assert sorted(tasks.interrupted) == sorted(
        (session_id, "服务关机，SDK client 已断开")
        for session_id in session_ids
    )
