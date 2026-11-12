import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery, ChannelInbound
from app.services import channel_delivery, channel_intake
from app.services.settings_store import (
    AgentSection,
    ChannelSection,
    ConversationSection,
    PlatformSettings,
)
from app.services.weixin_client import WeixinSessionExpired
from app.services.weixin_runtime import (
    INFLIGHT_BUSY_TEXT,
    MAX_FLUSH_RETRIES,
    RECOVERED_PREFIX,
    ChannelCredentials,
    PendingBatch,
    WeixinRuntime,
)
from app.services import weixin_runtime as weixin_runtime_module


@pytest.fixture(autouse=True)
async def _prepare_db():
    await init_db()
    yield


class FakeStore:
    """Settings store that hands out copies, like the real JSON-backed one."""

    def __init__(
        self,
        channels: list[ChannelSection] | None = None,
        conversations: dict[str, ConversationSection] | None = None,
    ) -> None:
        self.settings = PlatformSettings(
            agents=[AgentSection(id="default", name="Assistant", status="active")],
            channels=list(channels or []),
            channel_conversations=dict(conversations or {}),
        )
        self.channel_writes = 0

    async def get(self):
        return self.settings.model_copy(deep=True)

    async def update_channel(self, channel: ChannelSection) -> None:
        self.settings.channels = [
            item for item in self.settings.channels if item.id != channel.id
        ]
        self.settings.channels.append(channel.model_copy(deep=True))
        self.channel_writes += 1

    async def update_channel_fields(self, channel_id: str, **fields) -> None:
        for channel in self.settings.channels:
            if channel.id == channel_id:
                for key, value in fields.items():
                    setattr(channel, key, value)
                self.channel_writes += 1
                return

    async def update_conversation(
        self, key: str, conversation: ConversationSection
    ) -> None:
        self.settings.channel_conversations[key] = conversation.model_copy(
            deep=True
        )

    async def update_conversation_if_unchanged(
        self,
        key: str,
        expected_session_id: str | None,
        conversation: ConversationSection,
    ) -> bool:
        current = self.settings.channel_conversations.get(key)
        current_session = current.session_id if current is not None else None
        if current_session != expected_session_id:
            return False
        self.settings.channel_conversations[key] = conversation.model_copy(
            deep=True
        )
        return True


class FakeRegistry:
    def __init__(self) -> None:
        self.turns = []
        self.interrupted: list[str] = []
        self.busy_sessions: set[str] = set()

    async def submit(self, turn):
        self.turns.append(turn)
        # A submitted turn occupies its session until it finishes.
        self.busy_sessions.add(turn.session_id)
        return turn.id

    async def interrupt(self, session_id: str) -> bool:
        self.interrupted.append(session_id)
        return True

    def is_busy(self, session_id: str) -> bool:
        return session_id in self.busy_sessions


class FakeClient:
    def __init__(
        self,
        *,
        send_error: BaseException | None = None,
        send_event: asyncio.Event | None = None,
        updates_error: BaseException | None = None,
        media: bytes = b"",
    ) -> None:
        self.send_error = send_error
        self.send_event = send_event
        self.updates_error = updates_error
        self.media = media
        self.sent: list[tuple[str, str, str | None]] = []
        self.downloads: list[str] = []
        self.closed = False

    async def send_text(
        self, *, to_user_id, text, context_token=None, client_id=None
    ):
        if self.send_event is not None:
            await self.send_event.wait()
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((to_user_id, text, context_token))
        return {"ret": 0}

    async def get_updates(self, sync_buf: str):
        if self.updates_error is not None:
            raise self.updates_error
        await asyncio.sleep(3600)

    async def download(self, url: str, *, max_bytes: int) -> bytes:
        self.downloads.append(url)
        return self.media

    async def aclose(self) -> None:
        self.closed = True


def _channel(
    channel_id: str | None = None, *, status: str = "active"
) -> ChannelSection:
    return ChannelSection(
        id=channel_id or f"ch-{uuid.uuid4().hex[:10]}",
        agent_id="default",
        status=status,
        account_id="bot@im.bot",
        bot_token="token",
    )


def _credentials(channel: ChannelSection) -> ChannelCredentials:
    return ChannelCredentials(
        channel_id=channel.id,
        account_id=channel.account_id,
        token=channel.bot_token,
        base_url=channel.base_url or "https://ilinkai.weixin.qq.com",
        sync_buf="",
        agent_id=channel.agent_id,
    )


def _runtime(
    store: FakeStore,
    registry: FakeRegistry,
    *,
    client: FakeClient,
    batch_seconds: float = 60.0,
) -> WeixinRuntime:
    return WeixinRuntime(
        store=store,
        registry=registry,
        client_factory=lambda **kwargs: client,
        batch_seconds=batch_seconds,
        batch_max_seconds=batch_seconds,
    )


def _text_message(
    message_id: str,
    text: str,
    *,
    sender_id: str = "wx-1",
    context_token: str | None = None,
) -> dict:
    message = {
        "message_id": message_id,
        "from_user_id": sender_id,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }
    if context_token:
        message["context_token"] = context_token
    return message


async def _create_delivery(
    channel_id: str, *, external_id: str = "wx-1", content: str = "hello"
) -> int:
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db,
            channel_id=channel_id,
            external_id=external_id,
            session_id=str(uuid.uuid4()),
        )
        await db.commit()
    if content:
        assert await channel_delivery.complete(delivery_id, content)
    return delivery_id


async def _cleanup_deliveries(*delivery_ids: int) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelDelivery).where(ChannelDelivery.id.in_(delivery_ids))
        )
        await db.commit()


async def _cleanup_inbound(channel_id: str, message_ids: list[str]) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelInbound).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id.in_(message_ids),
            )
        )
        await db.commit()


async def _cleanup_channel_deliveries(channel_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelDelivery).where(
                ChannelDelivery.channel_id == channel_id
            )
        )
        await db.commit()


def _conversation(channel: ChannelSection, *, context_token: str | None) -> ConversationSection:
    return ConversationSection(
        session_id=str(uuid.uuid4()),
        channel_id=channel.id,
        display_name="Alice",
        context_token=context_token,
    )


def _stored_channel(store: FakeStore, channel_id: str) -> ChannelSection:
    return next(item for item in store.settings.channels if item.id == channel_id)


@pytest.mark.asyncio
async def test_flush_pending_submits_one_turn_for_the_batch():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    try:
        await runtime._handle_message(
            credentials,
            _text_message("batch-1", "第一句", context_token="ctx"),
        )
        await runtime._handle_message(
            credentials,
            _text_message("batch-2", "第二句", context_token="ctx"),
        )
        assert registry.turns == []

        await runtime.flush_pending((channel.id, "wx-1"))

        assert len(registry.turns) == 1
        turn = registry.turns[0]
        assert turn.source == "wechat"
        assert turn.prompt == "第一句\n\n第二句"
        # Intake pre-creates the queued delivery row it hands to the turn.
        assert turn.delivery.channel_id == channel.id
        assert turn.delivery.external_id == "wx-1"
        assert turn.delivery.delivery_id is not None
        conversation = store.settings.channel_conversations[
            f"{channel.id}:wx-1"
        ]
        assert conversation.session_id == turn.session_id
        assert conversation.context_token == "ctx"
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["batch-1", "batch-2"])
        await _cleanup_channel_deliveries(channel.id)


@pytest.mark.asyncio
async def test_already_claimed_id_does_not_drop_the_new_message_in_the_batch():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    try:
        assert await channel_intake.claim_inbound(
            channel.id, ["old-1"], text="旧消息", external_id="wx-1"
        )
        # A replay of the old id must not enter the batch, or the atomic
        # batch claim would treat the whole packet as a duplicate and swallow
        # the new message bundled with it.
        await runtime._handle_message(
            credentials, _text_message("old-1", "旧消息")
        )
        await runtime._handle_message(
            credentials, _text_message("new-1", "新消息")
        )
        await runtime.flush_pending((channel.id, "wx-1"))

        assert len(registry.turns) == 1
        assert registry.turns[0].prompt == "新消息"
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["old-1", "new-1"])


@pytest.mark.asyncio
async def test_new_command_flushes_pending_and_sends_reply_without_a_turn(monkeypatch):
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    calls: list[dict] = []
    real_ingest = channel_intake.ingest

    async def spy(**kwargs):
        calls.append(kwargs)
        return await real_ingest(**kwargs)

    monkeypatch.setattr(weixin_runtime_module.channel_intake, "ingest", spy)
    try:
        await runtime._handle_message(
            credentials, _text_message("cmd-buffer", "先说的话")
        )
        await runtime._handle_message(
            credentials,
            _text_message("cmd-new", "/new", context_token="ctx-cmd"),
            client,
        )

        # One ingest for the flushed batch, one for the command itself.
        assert len(calls) == 2
        assert calls[0]["external_message_ids"] == ["cmd-buffer"]
        assert calls[-1]["external_message_ids"] == ["cmd-new"]
        assert calls[-1]["text"] == "/new"

        # The buffered text produced the only turn; the command made none.
        assert len(registry.turns) == 1
        assert registry.turns[0].prompt == "先说的话"

        to_user_id, reply, context_token = client.sent[-1]
        assert to_user_id == "wx-1"
        assert "已开始新会话" in reply
        assert context_token == "ctx-cmd"
        assert runtime._pending == {}
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["cmd-buffer", "cmd-new"])


@pytest.mark.asyncio
async def test_stop_command_discards_pending_without_flushing():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    try:
        await runtime._handle_message(
            credentials, _text_message("cmd-buffer", "还没发出去的话")
        )
        await runtime._handle_message(
            credentials,
            _text_message("cmd-stop", "/stop", context_token="ctx-stop"),
            client,
        )

        # /stop discards the buffered packet instead of turning it into a Run.
        assert runtime._pending == {}
        assert registry.turns == []
        assert "已请求停止" in client.sent[-1][1]
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["cmd-buffer", "cmd-stop"])


@pytest.mark.asyncio
async def test_help_and_status_commands_leave_the_batch_window_alone():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    key = (channel.id, "wx-1")
    try:
        await runtime._handle_message(
            credentials, _text_message("cmd-buffer", "稍后一起发")
        )
        await runtime._handle_message(
            credentials,
            _text_message("cmd-status", "/status", context_token="ctx"),
            client,
        )
        await runtime._handle_message(
            credentials,
            _text_message("cmd-help", "/help", context_token="ctx"),
            client,
        )

        assert key in runtime._pending
        assert registry.turns == []
        replies = [text for _, text, _ in client.sent]
        assert any("状态" in reply for reply in replies)
        assert any("可用命令" in reply for reply in replies)
    finally:
        await runtime.stop()
        await _cleanup_inbound(
            channel.id, ["cmd-buffer", "cmd-status", "cmd-help"]
        )


@pytest.mark.asyncio
async def test_deliver_sends_chunks_and_marks_sent_once():
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore(
        [channel], {f"{channel.id}:wx-1": conversation}
    )
    client = FakeClient()
    runtime = _runtime(store, FakeRegistry(), client=client)
    content = "a" * 4000
    delivery_id = await _create_delivery(channel.id, content=content)
    try:
        await asyncio.gather(
            runtime.deliver(delivery_id),
            runtime.deliver(delivery_id),
        )

        assert len(client.sent) > 1
        assert "".join(text for _, text, _ in client.sent) == content
        assert all(token == "ctx" for _, _, token in client.sent)

        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "sent"
        assert row.delivered_at is not None
        # The concurrent second call must not claim/send the row again.
        assert row.attempts == 1
    finally:
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_deliver_skips_when_claim_fails(monkeypatch):
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    client = FakeClient()
    runtime = _runtime(store, FakeRegistry(), client=client)
    delivery_id = await _create_delivery(channel.id, content="hello")

    async def no_claim(_delivery_id: int) -> bool:
        return False

    monkeypatch.setattr(
        weixin_runtime_module.channel_delivery, "mark_attempting", no_claim
    )
    try:
        await runtime.deliver(delivery_id)

        assert client.sent == []
        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "pending"
        assert row.attempts == 0
    finally:
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_deliver_ignores_empty_content_without_state_change():
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    client = FakeClient()
    runtime = _runtime(store, FakeRegistry(), client=client)
    delivery_id = await _create_delivery(channel.id, content="")
    try:
        await runtime.deliver(delivery_id)

        assert client.sent == []
        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "pending"
        assert row.attempts == 0
    finally:
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_deliver_session_expired_keeps_row_pending_and_marks_channel():
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    client = FakeClient(send_error=WeixinSessionExpired("expired"))
    runtime = _runtime(store, FakeRegistry(), client=client)
    delivery_id = await _create_delivery(channel.id, content="hello")
    try:
        await runtime.deliver(delivery_id)

        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "pending"
        assert "expired" in (row.last_error or "")
        updated = store.settings.channels[0]
        assert updated.status == "expired"
        assert "expired" in (updated.last_error or "")
    finally:
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_handle_message_renders_resolved_quote():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    async with SessionLocal() as db:
        db.add(
            ChannelInbound(
                channel_id=channel.id,
                external_message_id="orig-quote",
                text="原来的问题",
                external_id="wx-1",
            )
        )
        await db.commit()
    try:
        await runtime._handle_message(
            credentials,
            {
                "message_id": "quote-reply-1",
                "from_user_id": "wx-1",
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {"text": "这条引用你看到了吗"},
                        "ref_msg": {
                            "title": "Alice",
                            "message_item": {
                                "type": 1,
                                "msg_id": "orig-quote",
                            },
                        },
                    }
                ],
            },
        )
        await runtime.flush_pending((channel.id, "wx-1"))

        assert len(registry.turns) == 1
        prompt = registry.turns[0].prompt
        assert "[引用 Alice]" in prompt
        assert "> 原来的问题" in prompt
        assert "这条引用你看到了吗" in prompt
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["quote-reply-1", "orig-quote"])


@pytest.mark.asyncio
async def test_handle_message_renders_quoted_bot_reply():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    target = datetime.now(timezone.utc).replace(microsecond=0)
    async with SessionLocal() as db:
        db.add(
            ChannelDelivery(
                channel_id=channel.id,
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
        await runtime._handle_message(
            credentials,
            {
                "message_id": "quote-bot-1",
                "from_user_id": "wx-1",
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {"text": "这条引用你看到了吗"},
                        "ref_msg": {
                            "message_item": {
                                "type": 0,
                                "create_time_ms": int(target.timestamp() * 1000),
                            }
                        },
                    }
                ],
            },
        )
        await runtime.flush_pending((channel.id, "wx-1"))

        assert len(registry.turns) == 1
        prompt = registry.turns[0].prompt
        assert "> 昨天的日报正文" in prompt
        assert "这条引用你看到了吗" in prompt
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["quote-bot-1"])
        await _cleanup_channel_deliveries(channel.id)


@pytest.mark.asyncio
async def test_handle_message_unresolved_quote_renders_unavailable():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    try:
        await runtime._handle_message(
            credentials,
            {
                "message_id": "quote-miss-1",
                "from_user_id": "wx-1",
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {"text": "引用丢了吗"},
                        "ref_msg": {
                            "message_item": {"type": 1, "msg_id": "unknown-id"}
                        },
                    }
                ],
            },
        )
        await runtime.flush_pending((channel.id, "wx-1"))

        assert len(registry.turns) == 1
        prompt = registry.turns[0].prompt
        assert "（引用原文不可用）" in prompt
        assert "引用丢了吗" in prompt
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["quote-miss-1"])


@pytest.mark.asyncio
async def test_unsupported_message_reply_is_claimed_once():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    message = {
        "message_id": "voice-1",
        "from_user_id": "wx-1",
        "item_list": [{"type": 3}],
        "context_token": "ctx-voice",
    }
    try:
        await runtime._handle_message(credentials, message, client)
        await runtime._handle_message(credentials, message, client)

        assert len(client.sent) == 1
        to_user_id, reply, context_token = client.sent[0]
        assert to_user_id == "wx-1"
        assert "暂不支持" in reply
        assert context_token == "ctx-voice"
    finally:
        await _cleanup_inbound(channel.id, ["voice-1"])


@pytest.mark.asyncio
async def test_sweep_deliveries_uses_recovered_for_attempting_rows(monkeypatch):
    channel = _channel()
    paused = _channel(status="paused")
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore(
        [channel, paused], {f"{channel.id}:wx-1": conversation}
    )
    client = FakeClient()
    runtime = _runtime(store, FakeRegistry(), client=client)
    pending_id = await _create_delivery(channel.id, content="pending reply")
    attempting_id = await _create_delivery(channel.id, content="attempt reply")
    inactive_id = await _create_delivery(paused.id, content="inactive reply")
    assert await channel_delivery.mark_attempting(attempting_id)

    calls: list[tuple[int, bool]] = []
    original = runtime.deliver

    async def spy(delivery_id: int, *, recovered: bool = False) -> None:
        calls.append((delivery_id, recovered))
        await original(delivery_id, recovered=recovered)

    monkeypatch.setattr(runtime, "deliver", spy)
    try:
        await runtime.sweep_deliveries()
        tasks = list(runtime._delivery_tasks)
        assert tasks
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

        assert (pending_id, False) in calls
        assert (attempting_id, True) in calls
        assert all(delivery_id != inactive_id for delivery_id, _ in calls)
        assert any(
            text.startswith(RECOVERED_PREFIX) for _, text, _ in client.sent
        )
        pending_row = await channel_delivery.get(pending_id)
        attempting_row = await channel_delivery.get(attempting_id)
        assert pending_row is not None and pending_row.status == "sent"
        assert attempting_row is not None and attempting_row.status == "sent"
        inactive_row = await channel_delivery.get(inactive_id)
        assert inactive_row is not None and inactive_row.status == "pending"
    finally:
        await _cleanup_deliveries(pending_id, attempting_id, inactive_id)


@pytest.mark.asyncio
async def test_sync_buf_saved_only_after_all_batches_are_claimed():
    channel = _channel()
    store = FakeStore([channel])
    runtime = _runtime(store, FakeRegistry(), client=FakeClient())
    credentials = _credentials(channel)
    key = (channel.id, "wx-1")
    try:
        await runtime._handle_message(
            credentials, _text_message("sync-1", "等等我")
        )
        runtime._pending_sync[channel.id] = "new-buffer"
        await runtime._save_sync_if_idle(channel.id)
        assert _stored_channel(store, channel.id).sync_buf == ""

        await runtime.flush_pending(key)

        assert _stored_channel(store, channel.id).sync_buf == "new-buffer"
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["sync-1"])


@pytest.mark.asyncio
async def test_permanent_ingest_error_drops_batch_without_saving_cursor(monkeypatch):
    channel = _channel()
    store = FakeStore([channel])
    runtime = _runtime(store, FakeRegistry(), client=FakeClient())
    credentials = _credentials(channel)
    key = (channel.id, "wx-1")
    try:
        await runtime._handle_message(
            credentials, _text_message("paused-1", "你好")
        )
        runtime._pending_sync[channel.id] = "new-buffer"
        # The channel is paused after the packet was buffered, so intake raises
        # ValueError("channel is not active") on flush.
        _stored_channel(store, channel.id).status = "paused"
        scheduled: list[tuple] = []
        monkeypatch.setattr(
            runtime, "_schedule_flush", lambda *args, **kwargs: scheduled.append(args)
        )

        await runtime.flush_pending(key)

        assert key not in runtime._pending
        assert scheduled == []
        assert _stored_channel(store, channel.id).sync_buf == ""
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["paused-1"])


@pytest.mark.asyncio
async def test_transient_ingest_errors_retry_then_drop(monkeypatch):
    channel = _channel()
    store = FakeStore([channel])
    runtime = _runtime(store, FakeRegistry(), client=FakeClient())
    key = (channel.id, "wx-1")

    async def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(weixin_runtime_module.channel_intake, "ingest", boom)
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        runtime, "_schedule_flush", lambda *args, **kwargs: scheduled.append(args)
    )
    batch = PendingBatch(
        texts=["hi"],
        media=[],
        ids=["retry-1"],
        sender_name="Alice",
        context_token=None,
        first_at=0.0,
        last_at=0.0,
    )
    runtime._pending[key] = batch

    await runtime.flush_pending(key)
    assert key in runtime._pending
    assert runtime._pending[key].retries == 1
    assert len(scheduled) == 1

    batch.retries = MAX_FLUSH_RETRIES
    await runtime.flush_pending(key)
    assert key not in runtime._pending


@pytest.mark.asyncio
async def test_login_expiry_stops_poll_and_drops_buffered_packets():
    channel = _channel()
    store = FakeStore([channel])
    client = FakeClient(updates_error=WeixinSessionExpired("expired"))
    runtime = _runtime(store, FakeRegistry(), client=client)
    key = (channel.id, "wx-1")
    runtime._pending[key] = PendingBatch(
        texts=["before expiry"],
        media=[],
        ids=["exp-1"],
        sender_name="Alice",
        context_token=None,
        first_at=0.0,
        last_at=0.0,
    )
    try:
        assert await runtime.start_channel(channel.id) is True
        await asyncio.sleep(0.1)

        assert channel.id not in runtime.running_channels()
        assert runtime._pending == {}
        updated = _stored_channel(store, channel.id)
        assert updated.status == "expired"
        assert updated.last_error
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["exp-1"])


@pytest.mark.asyncio
async def test_start_and_stop_poll_task():
    channel = _channel()
    store = FakeStore([channel])
    client = FakeClient()
    runtime = _runtime(store, FakeRegistry(), client=client)
    try:
        assert await runtime.start_channel(channel.id) is True
        first = runtime._poll_tasks[channel.id]
        assert channel.id in runtime.running_channels()
        # A second activation must reuse the running poll task.
        assert await runtime.start_channel(channel.id) is True
        assert runtime._poll_tasks[channel.id] is first

        await runtime.stop_channel(channel.id)
        assert channel.id not in runtime.running_channels()
        assert client.closed is True
    finally:
        await runtime.stop()

    paused = _channel(status="paused")
    paused_store = FakeStore([paused])
    paused_runtime = _runtime(paused_store, FakeRegistry(), client=FakeClient())
    assert await paused_runtime.start_channel(paused.id) is False


@pytest.mark.asyncio
async def test_media_and_quote_merge_into_one_batch():
    channel = _channel()
    store = FakeStore([channel])
    registry = FakeRegistry()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    client = FakeClient(media=png)
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    try:
        await runtime._handle_message(
            credentials,
            {
                "message_id": "media-quote-1",
                "from_user_id": "wx-1",
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {"text": "这张图是什么"},
                        "ref_msg": {
                            "title": "Alice",
                            "message_item": {
                                "type": 1,
                                "text_item": {"text": "原来的问题"},
                            },
                        },
                    },
                    {
                        "type": 2,
                        "image_item": {
                            "media": {"full_url": "https://cdn.example.com/a.png"}
                        },
                    },
                ],
            },
            client,
        )
        await runtime.flush_pending((channel.id, "wx-1"))

        assert len(registry.turns) == 1
        prompt = registry.turns[0].prompt
        assert "附件已上传到工作区" in prompt
        assert "这张图是什么" in prompt
        assert "[引用 Alice]" in prompt
        assert "原来的问题" in prompt
        assert "uploads/wx-" in prompt
        assert "/uploads/" not in prompt
        assert client.downloads == ["https://cdn.example.com/a.png"]
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["media-quote-1"])


@pytest.mark.asyncio
async def test_slow_delivery_does_not_block_the_enqueue_callback():
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    send_event = asyncio.Event()
    client = FakeClient(send_event=send_event)
    runtime = _runtime(store, FakeRegistry(), client=client)
    delivery_id = await _create_delivery(channel.id, content="hello")
    try:
        async def enqueue(identifier: int) -> None:
            runtime.schedule_delivery(identifier)

        started = time.monotonic()
        await asyncio.wait_for(enqueue(delivery_id), timeout=0.5)
        assert time.monotonic() - started < 0.5

        tasks = list(runtime._delivery_tasks)
        assert tasks
        await asyncio.sleep(0)
        assert client.sent == []

        send_event.set()
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert client.sent == [("wx-1", "hello", "ctx")]
        assert runtime._delivery_tasks == set()
    finally:
        send_event.set()
        await runtime.stop()
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_cancelled_flush_does_not_save_the_sync_cursor(monkeypatch):
    channel = _channel()
    store = FakeStore([channel])
    runtime = _runtime(store, FakeRegistry(), client=FakeClient())
    credentials = _credentials(channel)
    key = (channel.id, "wx-1")
    started = asyncio.Event()

    async def slow_ingest(**kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        weixin_runtime_module.channel_intake, "ingest", slow_ingest
    )
    try:
        await runtime._handle_message(
            credentials, _text_message("cancel-1", "还没处理完")
        )
        runtime._pending_sync[channel.id] = "new-buffer"
        task = asyncio.create_task(runtime.flush_pending(key))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert _stored_channel(store, channel.id).sync_buf == ""
        assert key not in runtime._inflight
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["cancel-1"])


class PartialSendClient(FakeClient):
    """Fails after the first accepted chunk to simulate a mid-send failure."""

    def __init__(self, *, error: BaseException, fail_after: int = 1, **kwargs) -> None:
        super().__init__(**kwargs)
        self._error = error
        self._fail_after = fail_after

    async def send_text(self, *, to_user_id, text, context_token=None, client_id=None):
        if len(self.sent) >= self._fail_after:
            raise self._error
        self.sent.append((to_user_id, text, context_token))
        return {"ret": 0}


@pytest.mark.asyncio
async def test_partial_delivery_failure_stays_attempting_for_prefix_retry(monkeypatch):
    monkeypatch.setattr(weixin_runtime_module, "RETRY_DELAY_SECONDS", 0)
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    client = PartialSendClient(error=RuntimeError("second chunk failed"))
    runtime = _runtime(store, FakeRegistry(), client=client)
    delivery_id = await _create_delivery(channel.id, content="a" * 4000)
    try:
        await runtime.deliver(delivery_id)

        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert len(client.sent) == 1
        # Keeping "attempting" is what makes the next sweep prepend the
        # "possibly partially delivered" notice.
        assert row.status == "attempting"
        assert row.attempts == 1
        assert "second chunk failed" in (row.last_error or "")
    finally:
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_session_expiry_after_partial_send_also_keeps_attempting(monkeypatch):
    monkeypatch.setattr(weixin_runtime_module, "RETRY_DELAY_SECONDS", 0)
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    client = PartialSendClient(error=WeixinSessionExpired("expired"))
    runtime = _runtime(store, FakeRegistry(), client=client)
    delivery_id = await _create_delivery(channel.id, content="a" * 4000)
    try:
        await runtime.deliver(delivery_id)

        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "attempting"
        assert "expired" in (row.last_error or "")
        assert _stored_channel(store, channel.id).status == "expired"
    finally:
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_failure_with_no_chunk_sent_returns_to_pending(monkeypatch):
    monkeypatch.setattr(weixin_runtime_module, "RETRY_DELAY_SECONDS", 0)
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    client = PartialSendClient(error=RuntimeError("never sent"), fail_after=0)
    runtime = _runtime(store, FakeRegistry(), client=client)
    delivery_id = await _create_delivery(channel.id, content="hello")
    try:
        await runtime.deliver(delivery_id)

        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "pending"
    finally:
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_new_command_waits_for_an_inflight_batch(monkeypatch):
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    key = (channel.id, "wx-1")
    started = asyncio.Event()
    release = asyncio.Event()
    real_ingest = channel_intake.ingest

    async def gated_ingest(**kwargs):
        if kwargs["external_message_ids"] == ["inflight-1"]:
            started.set()
            await release.wait()
        return await real_ingest(**kwargs)

    monkeypatch.setattr(weixin_runtime_module.channel_intake, "ingest", gated_ingest)
    old_session = conversation.session_id
    # Keep the binding valid while the batch is still in flight; otherwise the
    # runtime would treat the not-yet-written transcript as a stale binding.
    registry.busy_sessions.add(old_session)
    try:
        await runtime._handle_message(
            credentials, _text_message("inflight-1", "旧会话里的话")
        )
        flush = asyncio.create_task(runtime.flush_pending(key))
        await asyncio.wait_for(started.wait(), 1)

        command = asyncio.create_task(
            runtime._handle_message(
                credentials, _text_message("cmd-new", "/new", context_token="ctx")
            )
        )
        await asyncio.sleep(0.05)
        # The command must not rebind the contact while the batch is landing.
        assert not command.done()
        assert store.settings.channel_conversations[
            f"{channel.id}:wx-1"
        ].session_id == old_session

        release.set()
        await asyncio.wait_for(flush, 1)
        await asyncio.wait_for(command, 1)

        # The buffered text stayed in the old session; the command started a
        # new one.
        assert len(registry.turns) == 1
        assert registry.turns[0].session_id == old_session
        assert store.settings.channel_conversations[
            f"{channel.id}:wx-1"
        ].session_id != old_session
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["inflight-1", "cmd-new"])


@pytest.mark.asyncio
async def test_stop_command_waits_then_interrupts_the_inflight_session(monkeypatch):
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    key = (channel.id, "wx-1")
    started = asyncio.Event()
    release = asyncio.Event()
    real_ingest = channel_intake.ingest

    async def gated_ingest(**kwargs):
        if kwargs["external_message_ids"] == ["inflight-stop"]:
            started.set()
            await release.wait()
        return await real_ingest(**kwargs)

    monkeypatch.setattr(weixin_runtime_module.channel_intake, "ingest", gated_ingest)
    old_session = conversation.session_id
    registry.busy_sessions.add(old_session)
    try:
        await runtime._handle_message(
            credentials, _text_message("inflight-stop", "先执行这句")
        )
        flush = asyncio.create_task(runtime.flush_pending(key))
        await asyncio.wait_for(started.wait(), 1)

        command = asyncio.create_task(
            runtime._handle_message(
                credentials, _text_message("cmd-stop", "/stop", context_token="ctx")
            )
        )
        await asyncio.sleep(0.05)
        assert not command.done()

        release.set()
        await asyncio.wait_for(flush, 1)
        await asyncio.wait_for(command, 1)

        assert old_session in registry.interrupted
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["inflight-stop", "cmd-stop"])


@pytest.mark.asyncio
async def test_start_channel_refuses_inactive_agent():
    channel = _channel()
    store = FakeStore([channel])
    store.settings.agents[0].status = "paused"
    runtime = _runtime(store, FakeRegistry(), client=FakeClient())
    try:
        assert await runtime.start_channel(channel.id) is False
        assert runtime.running_channels() == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_poll_loop_pauses_when_agent_becomes_inactive():
    channel = _channel()
    store = FakeStore([channel])
    credentials = _credentials(channel)
    runtime = _runtime(store, FakeRegistry(), client=FakeClient())
    store.settings.agents[0].status = "archived"
    try:
        await asyncio.wait_for(
            runtime._poll_loop(credentials, FakeClient()), 1
        )
        assert runtime.running_channels() == []
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_partial_delivery_reaches_terminal_failed_at_attempt_limit(monkeypatch):
    monkeypatch.setattr(weixin_runtime_module, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        weixin_runtime_module.settings, "channel_delivery_max_attempts", 2
    )
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    client = PartialSendClient(error=RuntimeError("second chunk failed"))
    runtime = _runtime(store, FakeRegistry(), client=client)
    delivery_id = await _create_delivery(channel.id, content="a" * 4000)
    try:
        # First failed send: one chunk went out, the claim counted attempt 1.
        await runtime.deliver(delivery_id)
        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "attempting"
        assert row.attempts == 1

        # Second failed send reaches the limit and becomes terminal.
        client.sent.clear()
        await runtime.deliver(delivery_id, recovered=True)
        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "failed"
        assert row.attempts == 2
        assert "second chunk failed" in (row.last_error or "")
        # Terminal rows are no longer retried by the startup sweep.
        pending = await channel_delivery.list_pending()
        assert all(item.id != delivery_id for item in pending)
    finally:
        await _cleanup_deliveries(delivery_id)


@pytest.mark.asyncio
async def test_wait_inflight_times_out_and_returns_false(monkeypatch):
    channel = _channel()
    store = FakeStore([channel])
    runtime = _runtime(store, FakeRegistry(), client=FakeClient())
    key = (channel.id, "wx-1")
    event = asyncio.Event()
    runtime._inflight_events[key] = event
    monkeypatch.setattr(weixin_runtime_module, "INFLIGHT_WAIT_SECONDS", 0.01)
    try:
        assert await runtime._wait_inflight(key) is False
    finally:
        runtime._inflight_events.pop(key, None)
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/new", "/stop"])
async def test_inflight_timeout_refuses_command_without_rebinding(
    monkeypatch, command
):
    channel = _channel()
    conversation = _conversation(channel, context_token="ctx")
    store = FakeStore([channel], {f"{channel.id}:wx-1": conversation})
    registry = FakeRegistry()
    client = FakeClient()
    runtime = _runtime(store, registry, client=client)
    credentials = _credentials(channel)
    key = (channel.id, "wx-1")
    runtime._inflight_events[key] = asyncio.Event()  # never completes
    monkeypatch.setattr(weixin_runtime_module, "INFLIGHT_WAIT_SECONDS", 0.01)
    old_session = conversation.session_id
    message_id = f"cmd-{command.strip('/')}"
    try:
        await runtime._handle_message(
            credentials,
            _text_message(message_id, command, context_token="ctx"),
            client,
        )

        assert client.sent[-1][1] == INFLIGHT_BUSY_TEXT
        assert registry.turns == []
        assert registry.interrupted == []
        assert store.settings.channel_conversations[
            f"{channel.id}:wx-1"
        ].session_id == old_session
    finally:
        runtime._inflight_events.pop(key, None)
        await runtime.stop()
        await _cleanup_inbound(channel.id, [message_id])
