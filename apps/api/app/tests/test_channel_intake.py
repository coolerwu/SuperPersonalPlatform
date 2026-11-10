import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import ChannelInbound
from app.services.channel_intake import (
    HELP_TEXT,
    NEW_SESSION_TEXT,
    STOP_REQUESTED_TEXT,
    ingest,
)
from app.services.settings_store import (
    AgentSection,
    ChannelSection,
    PlatformSettings,
)


class FakeStore:
    def __init__(self):
        self.settings = PlatformSettings(
            agents=[AgentSection(id="default", name="Assistant")],
            channels=[
                ChannelSection(
                    id="ch1",
                    agent_id="default",
                    status="active",
                    account_id="a1",
                    bot_token="t",
                )
            ],
            channel_conversations={},
        )

    async def get(self):
        return self.settings

    async def update_conversation(self, external_id, conversation):
        self.settings.channel_conversations[external_id] = conversation


class FakeRegistry:
    def __init__(self):
        self.turns = []
        self.interrupted = []
        self.interrupt_result = True

    async def submit(self, turn):
        self.turns.append(turn)
        return turn.id

    async def interrupt(self, session_id):
        self.interrupted.append(session_id)
        return self.interrupt_result

    def is_busy(self, session_id):
        return False


async def _cleanup(message_ids):
    """Remove only the idempotency rows this test created."""
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelInbound).where(
                ChannelInbound.channel_id == "ch1",
                ChannelInbound.external_message_id.in_(message_ids),
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_first_message_creates_conversation_and_turn():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        result = await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="first-1",
            external_id="wx-1",
            display_name="张三",
            text="你好",
            context_token="ctx",
        )
        assert result.session_id
        assert registry.turns[0].prompt == "你好"
        assert registry.turns[0].delivery.external_id == "wx-1"
        assert registry.turns[0].source == "wechat"
        assert store.settings.channel_conversations["wx-1"].context_token == "ctx"
    finally:
        await _cleanup(["first-1"])


@pytest.mark.asyncio
async def test_duplicate_is_ignored():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="dup-1",
            external_id="wx-1",
            display_name="张三",
            text="你好",
            context_token="ctx",
        )
        result = await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="dup-1",
            external_id="wx-1",
            display_name="张三",
            text="你好",
            context_token="ctx",
        )
        assert result.duplicate is True
        assert result.session_id is None
        assert len(registry.turns) == 1
    finally:
        await _cleanup(["dup-1"])


@pytest.mark.asyncio
async def test_help_command():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        result = await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="help-1",
            external_id="wx-1",
            display_name="张三",
            text="/help",
            context_token=None,
        )
        assert result.reply == HELP_TEXT
        assert result.session_id
        assert registry.turns == []
    finally:
        await _cleanup(["help-1"])


@pytest.mark.asyncio
async def test_new_command_rebinds_conversation():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        first = await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="new-1",
            external_id="wx-1",
            display_name="张三",
            text="你好",
            context_token="ctx",
        )
        session_a = first.session_id
        assert session_a
        assert store.settings.channel_conversations["wx-1"].session_id == session_a

        created = await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="new-2",
            external_id="wx-1",
            display_name="张三",
            text="/new",
            context_token="ctx-2",
        )
        session_b = created.session_id
        assert created.reply == NEW_SESSION_TEXT
        assert session_b
        assert session_b != session_a
        conversation = store.settings.channel_conversations["wx-1"]
        assert conversation.session_id == session_b
        # The conversation UUID is regenerated, not reused.
        assert conversation.channel_id == "ch1"
        assert conversation.context_token == "ctx-2"
        # /new itself does not start a turn.
        assert len(registry.turns) == 1

        follow_up = await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="new-3",
            external_id="wx-1",
            display_name="张三",
            text="继续",
            context_token=None,
        )
        assert follow_up.session_id == session_b
        assert registry.turns[-1].session_id == session_b
        assert registry.turns[-1].prompt == "继续"
    finally:
        await _cleanup(["new-1", "new-2", "new-3"])


@pytest.mark.asyncio
async def test_stop_command_delegates_to_registry():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        started = await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="stop-1",
            external_id="wx-1",
            display_name="张三",
            text="跑一个长任务",
            context_token="ctx",
        )
        session_id = started.session_id
        assert session_id
        assert len(registry.turns) == 1

        stopped = await ingest(
            store=store,
            registry=registry,
            channel_id="ch1",
            external_message_id="stop-2",
            external_id="wx-1",
            display_name="张三",
            text="/stop",
            context_token="ctx",
        )
        assert stopped.reply == STOP_REQUESTED_TEXT
        assert stopped.session_id == session_id
        assert registry.interrupted == [session_id]
        # /stop is a command, not a new turn.
        assert len(registry.turns) == 1
    finally:
        await _cleanup(["stop-1", "stop-2"])
