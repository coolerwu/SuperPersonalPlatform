import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery, ChannelInbound
from app.services import commands
from app.services.channel_intake import (
    inbound_seen,
    ingest,
    resolve_quoted_text,
)
from app.services.claude_runner import transcript_path
from app.services.settings_store import (
    AgentSection,
    ChannelSection,
    ConversationSection,
    PlatformSettings,
)


class FakeStore:
    """Settings store that hands out copies, like the real JSON-backed one."""

    def __init__(self, *, agent_status="active", channel_status="active"):
        self.settings = PlatformSettings(
            agents=[AgentSection(id="default", name="Assistant", status=agent_status)],
            channels=[
                ChannelSection(
                    id="ch1",
                    agent_id="default",
                    status=channel_status,
                    account_id="a1",
                    bot_token="t1",
                ),
                ChannelSection(
                    id="ch2",
                    agent_id="default",
                    status="active",
                    account_id="a2",
                    bot_token="t2",
                ),
            ],
            channel_conversations={},
        )

    async def get(self):
        return self.settings.model_copy(deep=True)

    async def update_conversation(self, external_id, conversation):
        self.settings.channel_conversations[external_id] = conversation.model_copy(deep=True)

    async def update_conversation_if_unchanged(
        self, external_id, expected_session_id, conversation
    ):
        current = self.settings.channel_conversations.get(external_id)
        current_session = current.session_id if current is not None else None
        if current_session != expected_session_id:
            return False
        self.settings.channel_conversations[external_id] = conversation.model_copy(
            deep=True
        )
        return True


class RacingStore(FakeStore):
    """Rebinds a contact right after the first settings read.

    Simulates another inbound turn (e.g. /new) winning the write between
    ingest's read and compare-and-set.
    """

    def __init__(
        self,
        *,
        session_id: str,
        key: str = "ch1:wx-1",
        channel_id: str = "ch1",
    ) -> None:
        super().__init__()
        self._race_session_id = session_id
        self._race_key = key
        self._race_channel_id = channel_id
        self._raced = False

    async def get(self):
        snapshot = await super().get()
        if not self._raced:
            self._raced = True
            self.settings.channel_conversations[
                self._race_key
            ] = ConversationSection(
                session_id=self._race_session_id,
                channel_id=self._race_channel_id,
                display_name="Alice",
                context_token="ctx-race",
            )
        return snapshot


class FakeRegistry:
    def __init__(self, *, busy=False, interrupt_result=True):
        self.turns = []
        self.interrupted = []
        self.busy = busy
        self.busy_sessions = set()
        self.interrupt_result = interrupt_result
        self.submit_error = None

    async def submit(self, turn):
        if self.submit_error is not None:
            raise self.submit_error
        self.turns.append(turn)
        # A submitted turn occupies its session until it finishes.
        self.busy_sessions.add(turn.session_id)
        return turn.id

    async def interrupt(self, session_id):
        self.interrupted.append(session_id)
        return self.interrupt_result

    def is_busy(self, session_id):
        return self.busy or session_id in self.busy_sessions


async def _ingest(
    store,
    registry,
    *,
    message_ids,
    text="你好",
    channel_id="ch1",
    external_id="wx-1",
    display_name="张三",
    context_token="ctx",
):
    return await ingest(
        store=store,
        registry=registry,
        channel_id=channel_id,
        external_message_ids=message_ids,
        external_id=external_id,
        display_name=display_name,
        text=text,
        context_token=context_token,
    )


async def _cleanup(channel_id, message_ids):
    """Remove only the idempotency rows this test created."""
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelInbound).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id.in_(message_ids),
            )
        )
        await db.commit()


async def _rows(channel_id, message_ids):
    async with SessionLocal() as db:
        result = await db.scalars(
            select(ChannelInbound).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id.in_(message_ids),
            )
        )
        return list(result)


def _create_transcript(agent_id, session_id):
    path = transcript_path(session_id, agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", "utf-8")
    return path


def _conversation_key(channel_id, external_id):
    return f"{channel_id}:{external_id}"


@pytest.mark.asyncio
async def test_first_message_creates_conversation_and_turn():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        result = await _ingest(store, registry, message_ids=["first-1"])
        assert result.session_id
        assert registry.turns[0].prompt == "你好"
        assert registry.turns[0].delivery.external_id == "wx-1"
        assert registry.turns[0].source == "wechat"
        assert registry.turns[0].session_id == result.session_id
        conversation = store.settings.channel_conversations[_conversation_key("ch1", "wx-1")]
        assert conversation.session_id == result.session_id
        assert conversation.channel_id == "ch1"
        assert conversation.context_token == "ctx"
    finally:
        await _cleanup("ch1", ["first-1"])


@pytest.mark.asyncio
async def test_text_is_stripped_before_submit():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        await _ingest(store, registry, message_ids=["trim-1"], text="  你好  ")
        assert registry.turns[0].prompt == "你好"
    finally:
        await _cleanup("ch1", ["trim-1"])


@pytest.mark.asyncio
async def test_duplicate_is_ignored():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        await _ingest(store, registry, message_ids=["dup-1"])
        result = await _ingest(store, registry, message_ids=["dup-1"])
        assert result.duplicate is True
        assert result.session_id is None
        assert len(registry.turns) == 1
    finally:
        await _cleanup("ch1", ["dup-1"])


@pytest.mark.asyncio
async def test_batch_claims_every_message_id():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    batch = ["batch-1", "batch-2", "batch-3"]
    try:
        result = await _ingest(store, registry, message_ids=batch)
        rows = await _rows("ch1", batch)
        assert len(rows) == 3
        assert {row.session_id for row in rows} == {result.session_id}
        assert len(registry.turns) == 1

        replay = await _ingest(store, registry, message_ids=batch)
        assert replay.duplicate is True

        # A later slice of the same batch (iLink replay of leftover ids) is
        # already claimed too, so it cannot start a second turn.
        tail = await _ingest(store, registry, message_ids=batch[1:])
        assert tail.duplicate is True
        assert len(registry.turns) == 1
    finally:
        await _cleanup("ch1", batch)


@pytest.mark.asyncio
async def test_help_command():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        result = await _ingest(store, registry, message_ids=["help-1"], text="/help")
        assert result.reply == commands.HELP_TEXT
        assert result.session_id
        assert registry.turns == []
    finally:
        await _cleanup("ch1", ["help-1"])


@pytest.mark.asyncio
async def test_new_command_rebinds_conversation():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    ids = ["new-1", "new-2", "new-3"]
    transcript = None
    try:
        first = await _ingest(store, registry, message_ids=["new-1"])
        session_a = first.session_id
        assert session_a

        created = await _ingest(
            store, registry, message_ids=["new-2"], text="/new", context_token="ctx-2"
        )
        session_b = created.session_id
        assert created.reply == commands.NEW_SESSION_TEXT
        assert session_b
        assert session_b != session_a
        conversation = store.settings.channel_conversations[_conversation_key("ch1", "wx-1")]
        assert conversation.session_id == session_b
        assert conversation.channel_id == "ch1"
        assert conversation.context_token == "ctx-2"
        assert len(registry.turns) == 1

        # The SDK writes the transcript once a turn runs in the new session.
        transcript = _create_transcript("default", session_b)
        follow_up = await _ingest(store, registry, message_ids=["new-3"], text="继续", context_token=None)
        assert follow_up.session_id == session_b
        assert registry.turns[-1].session_id == session_b
        assert registry.turns[-1].prompt == "继续"
    finally:
        if transcript is not None:
            transcript.unlink(missing_ok=True)
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_clear_command_rebinds_conversation():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    ids = ["clear-1", "clear-2"]
    try:
        first = await _ingest(store, registry, message_ids=["clear-1"])
        cleared = await _ingest(store, registry, message_ids=["clear-2"], text="/clear")
        assert cleared.reply == commands.NEW_SESSION_TEXT
        assert cleared.session_id != first.session_id
        assert (
            store.settings.channel_conversations[_conversation_key("ch1", "wx-1")].session_id
            == cleared.session_id
        )
    finally:
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_new_command_keeps_existing_context_token():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    ids = ["token-1", "token-2"]
    try:
        await _ingest(store, registry, message_ids=["token-1"], context_token="ctx-old")
        await _ingest(store, registry, message_ids=["token-2"], text="/new", context_token=None)
        conversation = store.settings.channel_conversations[_conversation_key("ch1", "wx-1")]
        assert conversation.context_token == "ctx-old"
    finally:
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_status_command_reports_busy_and_idle():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry(busy=True)
    ids = ["status-1", "status-2", "status-3"]
    transcript = None
    try:
        started = await _ingest(store, registry, message_ids=["status-1"])
        transcript = _create_transcript("default", started.session_id)
        busy = await _ingest(store, registry, message_ids=["status-2"], text="/status")
        assert "执行中" in busy.reply
        assert "default" in busy.reply
        assert started.session_id in busy.reply

        registry.busy = False
        registry.busy_sessions.clear()
        idle = await _ingest(store, registry, message_ids=["status-3"], text="/status")
        assert "空闲" in idle.reply
        assert len(registry.turns) == 1
    finally:
        if transcript is not None:
            transcript.unlink(missing_ok=True)
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_memory_and_unknown_commands_are_unknown():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    ids = ["unknown-1", "unknown-2"]
    try:
        memory = await _ingest(store, registry, message_ids=["unknown-1"], text="/memory 记一下")
        assert memory.reply == commands.UNKNOWN_COMMAND_TEXT
        unknown = await _ingest(store, registry, message_ids=["unknown-2"], text="/foo")
        assert unknown.reply == commands.UNKNOWN_COMMAND_TEXT
        assert registry.turns == []
    finally:
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_stop_command_delegates_to_registry():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    ids = ["stop-1", "stop-2"]
    transcript = None
    try:
        started = await _ingest(store, registry, message_ids=["stop-1"], text="跑一个长任务")
        session_id = started.session_id
        assert session_id
        transcript = _create_transcript("default", session_id)

        stopped = await _ingest(store, registry, message_ids=["stop-2"], text="/stop")
        assert stopped.reply == commands.STOP_REQUESTED_TEXT
        assert stopped.session_id == session_id
        assert registry.interrupted == [session_id]
        assert len(registry.turns) == 1
    finally:
        if transcript is not None:
            transcript.unlink(missing_ok=True)
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_stop_without_active_turn_reports_empty():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry(interrupt_result=False)
    ids = ["stop-idle-1", "stop-idle-2"]
    try:
        await _ingest(store, registry, message_ids=["stop-idle-1"])
        stopped = await _ingest(store, registry, message_ids=["stop-idle-2"], text="/stop")
        assert stopped.reply == commands.STOP_EMPTY_TEXT
        assert len(registry.interrupted) == 1
    finally:
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_inactive_channel_is_rejected_before_claim():
    await init_db()
    store = FakeStore(channel_status="pairing")
    registry = FakeRegistry()
    ids = ["inactive-channel-1"]
    try:
        with pytest.raises(ValueError, match="channel is not active"):
            await _ingest(store, registry, message_ids=ids)
        assert await _rows("ch1", ids) == []

        # Activation makes the same message id retryable.
        store.settings.channels[0].status = "active"
        result = await _ingest(store, registry, message_ids=ids)
        assert result.session_id
        assert len(registry.turns) == 1
    finally:
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_inactive_agent_is_rejected_before_claim():
    await init_db()
    store = FakeStore(agent_status="paused")
    registry = FakeRegistry()
    ids = ["inactive-agent-1"]
    try:
        with pytest.raises(ValueError, match="agent is not available"):
            await _ingest(store, registry, message_ids=ids)
        assert await _rows("ch1", ids) == []
    finally:
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_submit_failure_releases_claim():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    registry.submit_error = RuntimeError("registry down")
    ids = ["submit-fail-1", "submit-fail-2"]
    try:
        with pytest.raises(RuntimeError, match="registry down"):
            await _ingest(store, registry, message_ids=ids)
        assert await _rows("ch1", ids) == []

        # The retry after the failure must be able to run.
        registry.submit_error = None
        result = await _ingest(store, registry, message_ids=ids)
        assert result.session_id
        assert len(registry.turns) == 1
    finally:
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_empty_inputs_are_rejected_before_claim():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    with pytest.raises(ValueError, match="text is required"):
        await _ingest(store, registry, message_ids=["empty-1"], text="   ")
    with pytest.raises(ValueError, match="external_id is required"):
        await _ingest(store, registry, message_ids=["empty-2"], external_id="  ")
    with pytest.raises(ValueError, match="external_message_ids"):
        await _ingest(store, registry, message_ids=[])
    with pytest.raises(ValueError, match="external_message_ids"):
        await _ingest(store, registry, message_ids=["ok-1", "   "])
    assert registry.turns == []


@pytest.mark.asyncio
async def test_deleted_transcript_rebinding_creates_new_session():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    stale_session = str(uuid.uuid4())
    store.settings.channel_conversations[_conversation_key("ch1", "wx-1")] = ConversationSection(
        session_id=stale_session,
        channel_id="ch1",
        display_name="张三",
        context_token="ctx-old",
    )
    try:
        result = await _ingest(store, registry, message_ids=["stale-1"], context_token=None)
        assert result.session_id
        assert result.session_id != stale_session
        conversation = store.settings.channel_conversations[_conversation_key("ch1", "wx-1")]
        assert conversation.session_id == result.session_id
        assert conversation.context_token == "ctx-old"
        assert registry.turns[0].session_id == result.session_id
    finally:
        await _cleanup("ch1", ["stale-1"])


@pytest.mark.asyncio
async def test_valid_existing_binding_is_reused():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    session_id = str(uuid.uuid4())
    path = _create_transcript("default", session_id)
    store.settings.channel_conversations[_conversation_key("ch1", "wx-1")] = ConversationSection(
        session_id=session_id,
        channel_id="ch1",
        display_name="旧名字",
        context_token="ctx-old",
    )
    try:
        result = await _ingest(
            store, registry, message_ids=["reuse-1"], display_name="新名字", context_token=None
        )
        assert result.session_id == session_id
        assert registry.turns[0].session_id == session_id
        conversation = store.settings.channel_conversations[_conversation_key("ch1", "wx-1")]
        assert conversation.display_name == "新名字"
        assert conversation.context_token == "ctx-old"
    finally:
        path.unlink(missing_ok=True)
        await _cleanup("ch1", ["reuse-1"])


@pytest.mark.asyncio
async def test_wrong_channel_binding_is_not_reused():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    foreign_session = str(uuid.uuid4())
    path = _create_transcript("default", foreign_session)
    key = _conversation_key("ch1", "wx-1")
    store.settings.channel_conversations[key] = ConversationSection(
        session_id=foreign_session,
        channel_id="ch2",
        display_name="别的渠道",
        context_token="ctx-old",
    )
    try:
        result = await _ingest(store, registry, message_ids=["foreign-1"], context_token=None)
        assert result.session_id != foreign_session
        conversation = store.settings.channel_conversations[key]
        assert conversation.channel_id == "ch1"
        assert conversation.session_id == result.session_id
        # A cross-channel binding must not leak its context token.
        assert conversation.context_token is None
    finally:
        path.unlink(missing_ok=True)
        await _cleanup("ch1", ["foreign-1"])


@pytest.mark.asyncio
async def test_same_external_id_in_two_channels_is_isolated():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        first = await _ingest(store, registry, message_ids=["cross-1"], channel_id="ch1")
        second = await _ingest(store, registry, message_ids=["cross-2"], channel_id="ch2")
        assert first.session_id
        assert second.session_id
        assert first.session_id != second.session_id
        by_key = store.settings.channel_conversations
        assert by_key[_conversation_key("ch1", "wx-1")].session_id == first.session_id
        assert by_key[_conversation_key("ch2", "wx-1")].session_id == second.session_id
        assert by_key[_conversation_key("ch2", "wx-1")].channel_id == "ch2"
    finally:
        await _cleanup("ch1", ["cross-1"])
        await _cleanup("ch2", ["cross-2"])


@pytest.mark.asyncio
async def test_queued_session_stays_valid_before_transcript():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    ids = ["queued-1", "queued-2", "queued-3", "queued-4"]
    try:
        first = await _ingest(store, registry, message_ids=["queued-1"])
        session_id = first.session_id
        assert session_id
        # The turn is queued/running but the SDK has not written a transcript
        # yet; the session is still valid because the registry owns it.
        assert not transcript_path(session_id, "default").is_file()
        assert registry.is_busy(session_id)

        second = await _ingest(store, registry, message_ids=["queued-2"], text="再来一条")
        assert second.session_id == session_id
        assert registry.turns[-1].session_id == session_id

        stopped = await _ingest(store, registry, message_ids=["queued-3"], text="/stop")
        assert stopped.reply == commands.STOP_REQUESTED_TEXT
        assert registry.interrupted == [session_id]

        status = await _ingest(store, registry, message_ids=["queued-4"], text="/status")
        assert "执行中" in status.reply
        assert session_id in status.reply
    finally:
        await _cleanup("ch1", ids)


@pytest.mark.asyncio
async def test_record_inbound_failure_keeps_claim(monkeypatch):
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    ids = ["record-1"]

    async def boom(*args, **kwargs):
        raise RuntimeError("db write failed")

    monkeypatch.setattr("app.services.channel_intake._record_inbound", boom)
    try:
        result = await _ingest(store, registry, message_ids=ids)
        # The turn is submitted and the reply is on its way: a bookkeeping
        # failure must not discard the claim and re-run the batch.
        assert result.session_id
        assert len(registry.turns) == 1
        rows = await _rows("ch1", ids)
        assert len(rows) == 1
        assert rows[0].session_id is None
    finally:
        await _cleanup("ch1", ids)


async def _cleanup_quote_rows(channel_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelInbound).where(ChannelInbound.channel_id == channel_id)
        )
        await db.execute(
            delete(ChannelDelivery).where(ChannelDelivery.channel_id == channel_id)
        )
        await db.commit()


@pytest.mark.asyncio
async def test_ingest_retries_when_binding_changes_between_read_and_write():
    await init_db()
    session_b = str(uuid.uuid4())
    _create_transcript("default", session_b)
    store = RacingStore(session_id=session_b)
    registry = FakeRegistry()
    try:
        result = await _ingest(
            store, registry, message_ids=["race-1"], text="新消息"
        )

        # The stale read must not write the old session back; the retry uses
        # the binding the concurrent turn installed.
        assert result.session_id == session_b
        binding = store.settings.channel_conversations["ch1:wx-1"]
        assert binding.session_id == session_b
        assert registry.turns[-1].session_id == session_b
    finally:
        await _cleanup("ch1", ["race-1"])
        transcript_path(session_b, "default").unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_inbound_seen_returns_already_claimed_ids():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        await _ingest(store, registry, message_ids=["seen-1"], text="旧消息")
        assert await inbound_seen("ch1", ["seen-1", "missing"]) == {"seen-1"}
        assert await inbound_seen("ch1", []) == set()
    finally:
        await _cleanup("ch1", ["seen-1"])


@pytest.mark.asyncio
async def test_resolve_quoted_text_by_message_id():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        await _ingest(store, registry, message_ids=["orig-1"], text="原来的问题")
        assert (
            await resolve_quoted_text("ch1", "wx-1", "orig-1", None)
            == "原来的问题"
        )
        # The exact id must still belong to the asking contact.
        assert (
            await resolve_quoted_text("ch1", "wx-2", "orig-1", None) is None
        )
        assert (
            await resolve_quoted_text("ch1", "wx-1", "missing", None) is None
        )
    finally:
        await _cleanup("ch1", ["orig-1"])


@pytest.mark.asyncio
async def test_resolve_quoted_text_by_inbound_time_window():
    await init_db()
    channel_id = f"q{uuid.uuid4().hex[:12]}"
    target = datetime.now(timezone.utc).replace(microsecond=0)
    async with SessionLocal() as db:
        db.add_all(
            [
                ChannelInbound(
                    channel_id=channel_id,
                    external_message_id="window-far",
                    text="较远的原文",
                    external_id="wx-1",
                    created_at=target - timedelta(seconds=1.8),
                ),
                ChannelInbound(
                    channel_id=channel_id,
                    external_message_id="window-near",
                    text="窗口内的原文",
                    external_id="wx-1",
                    created_at=target - timedelta(seconds=0.5),
                ),
            ]
        )
        await db.commit()
    try:
        assert (
            await resolve_quoted_text(
                channel_id,
                "wx-1",
                None,
                int(target.timestamp() * 1000),
            )
            == "窗口内的原文"
        )
    finally:
        await _cleanup_quote_rows(channel_id)


@pytest.mark.asyncio
async def test_resolve_quoted_text_by_bot_reply_time_window():
    await init_db()
    channel_id = f"q{uuid.uuid4().hex[:12]}"
    target = datetime.now(timezone.utc).replace(microsecond=0)
    async with SessionLocal() as db:
        db.add(
            ChannelDelivery(
                channel_id=channel_id,
                external_id="wx-1",
                session_id=str(uuid.uuid4()),
                content="昨天的日报正文",
                status="sent",
                created_at=target - timedelta(seconds=60),
                delivered_at=target - timedelta(seconds=1),
            )
        )
        await db.commit()
    try:
        assert (
            await resolve_quoted_text(
                channel_id,
                "wx-1",
                None,
                int(target.timestamp() * 1000),
            )
            == "昨天的日报正文"
        )
    finally:
        await _cleanup_quote_rows(channel_id)


@pytest.mark.asyncio
async def test_resolve_quoted_text_is_scoped_per_contact():
    await init_db()
    channel_id = f"q{uuid.uuid4().hex[:12]}"
    target = datetime.now(timezone.utc).replace(microsecond=0)
    async with SessionLocal() as db:
        db.add_all(
            [
                ChannelInbound(
                    channel_id=channel_id,
                    external_message_id="overlap-a",
                    text="联系人 A 的原文",
                    external_id="wx-a",
                    created_at=target - timedelta(seconds=1),
                ),
                ChannelInbound(
                    channel_id=channel_id,
                    external_message_id="overlap-b",
                    text="联系人 B 的原文",
                    external_id="wx-b",
                    created_at=target - timedelta(seconds=1),
                ),
            ]
        )
        await db.commit()
    try:
        assert (
            await resolve_quoted_text(
                channel_id,
                "wx-b",
                None,
                int(target.timestamp() * 1000),
            )
            == "联系人 B 的原文"
        )
        assert (
            await resolve_quoted_text(
                channel_id,
                "wx-a",
                None,
                int(target.timestamp() * 1000),
            )
            == "联系人 A 的原文"
        )
    finally:
        await _cleanup_quote_rows(channel_id)
