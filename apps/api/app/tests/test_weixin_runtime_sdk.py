import asyncio
import uuid

import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery, ChannelInbound
from app.services import channel_delivery, channel_intake
from app.services.claude_runner import DeliveryRequest
from app.services.settings_store import (
    AgentSection,
    ChannelSection,
    ConversationSection,
    PlatformSettings,
)
from app.services.weixin_client import WeixinSessionExpired
from app.services.weixin_runtime import (
    RECOVERED_PREFIX,
    ChannelCredentials,
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

    async def update_conversation(
        self, key: str, conversation: ConversationSection
    ) -> None:
        self.settings.channel_conversations[key] = conversation.model_copy(
            deep=True
        )


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
    def __init__(self, *, send_error: BaseException | None = None) -> None:
        self.send_error = send_error
        self.sent: list[tuple[str, str, str | None]] = []
        self.closed = False

    async def send_text(
        self, *, to_user_id, text, context_token=None, client_id=None
    ):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((to_user_id, text, context_token))
        return {"ret": 0}

    async def get_updates(self, sync_buf: str):
        await asyncio.sleep(3600)

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


def _conversation(channel: ChannelSection, *, context_token: str | None) -> ConversationSection:
    return ConversationSection(
        session_id=str(uuid.uuid4()),
        channel_id=channel.id,
        display_name="Alice",
        context_token=context_token,
    )


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
        assert turn.delivery == DeliveryRequest(
            channel_id=channel.id, external_id="wx-1"
        )
        conversation = store.settings.channel_conversations[
            f"{channel.id}:wx-1"
        ]
        assert conversation.session_id == turn.session_id
        assert conversation.context_token == "ctx"
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["batch-1", "batch-2"])


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
async def test_command_flushes_pending_and_sends_reply_without_a_turn(monkeypatch):
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
            _text_message("cmd-help", "/help", context_token="ctx-cmd"),
            client,
        )

        # One ingest for the flushed batch, one for the command itself.
        assert len(calls) == 2
        assert calls[0]["external_message_ids"] == ["cmd-buffer"]
        assert calls[-1]["external_message_ids"] == ["cmd-help"]
        assert calls[-1]["text"] == "/help"

        # The buffered text produced the only turn; the command made none.
        assert len(registry.turns) == 1
        assert registry.turns[0].prompt == "先说的话"

        to_user_id, reply, context_token = client.sent[-1]
        assert to_user_id == "wx-1"
        assert "可用命令" in reply
        assert context_token == "ctx-cmd"
        assert runtime._pending == {}
    finally:
        await runtime.stop()
        await _cleanup_inbound(channel.id, ["cmd-buffer", "cmd-help"])


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
