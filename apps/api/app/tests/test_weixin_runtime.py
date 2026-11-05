import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import Channel, ChannelDelivery, ChannelInbound, ChannelRunLink, Run, utcnow
from app.services.channel_service import ingest_inbound, split_reply
from app.services.run_service import complete_run
from app.services.weixin_client import WeixinApiError, WeixinSessionExpired
from app.services.weixin_runtime import (
    RECOVERED_PREFIX,
    ChannelCredentials,
    WeixinRuntime,
)


@pytest.fixture(autouse=True)
async def _cancel_wechat_runs():
    yield
    async with SessionLocal() as db:
        runs = (
            await db.scalars(
                select(Run).where(
                    Run.source == "wechat", Run.status.in_(("queued", "running"))
                )
            )
        ).all()
        for run in runs:
            run.status = "cancelled"
            run.finished_at = utcnow()
        await db.commit()


class FakeWorker:
    def __init__(self) -> None:
        self.wakes = 0
        self.cancelled: list[str] = []

    def wake(self) -> None:
        self.wakes += 1

    async def cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return True


class FakeClient:
    def __init__(self, *, updates=None, send_error=None, media=b"", **kwargs) -> None:
        self.updates = updates
        self.send_error = send_error
        self.media = media
        self.sent: list[tuple[str, str, str | None]] = []
        self.downloads: list[str] = []
        self.closed = False

    async def get_updates(self, sync_buf: str):
        if callable(self.updates):
            return await self.updates(sync_buf)
        if self.updates is not None:
            return self.updates
        await asyncio.sleep(3600)

    async def download(self, url: str, *, max_bytes: int) -> bytes:
        self.downloads.append(url)
        if len(self.media) > max_bytes:
            raise WeixinApiError("media too large")
        return self.media

    async def send_text(self, *, to_user_id, text, context_token=None, client_id=None):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((to_user_id, text, context_token))
        return {"ret": 0}

    async def aclose(self) -> None:
        self.closed = True


async def _active_channel() -> str:
    suffix = uuid.uuid4().hex[:10]
    async with SessionLocal() as db:
        channel = Channel(
            agent_id="default",
            type="wechat",
            name=f"runtime-{suffix}",
            status="active",
            account_id="bot@im.bot",
            bot_token="bot-token",
            base_url="https://ilinkai.weixin.qq.com",
        )
        db.add(channel)
        await db.commit()
        await db.refresh(channel)
        return channel.id


async def _delivery(channel_id: str, output: str = "hello") -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    result = await ingest_inbound(
        channel_id=channel_id,
        external_message_id=f"msg-{suffix}",
        external_conversation_id=f"user-{suffix}",
        conversation_name="Alice",
        text="hi",
        context_token="ctx",
    )
    assert result.run_id is not None
    await complete_run(
        result.run_id, output=output, input_tokens=0, output_tokens=0, total_tokens=0
    )
    return result.run_id, result.session_id or ""


def test_split_reply_respects_limit_and_boundaries() -> None:
    assert split_reply("short", max_chars=100) == ["short"]
    assert split_reply("", max_chars=100) == ["（无回复）"]

    text = "\n\n".join(["a" * 80, "b" * 80, "c" * 80])
    chunks = split_reply(text, max_chars=100)
    assert len(chunks) == 3
    assert all(len(chunk) <= 100 for chunk in chunks)

    long_text = "x" * 2500
    chunks = split_reply(long_text, max_chars=1000)
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunks) == long_text

    truncated = split_reply("y" * 20000, max_chars=1000)
    assert len(truncated) == 10
    assert truncated[-1].endswith("（内容过长，已截断）")


@pytest.mark.asyncio
async def test_handle_message_filters_self_groups_and_media() -> None:
    channel_id = await _active_channel()
    worker = FakeWorker()
    runtime = WeixinRuntime(
        worker=worker, client_factory=lambda **kwargs: FakeClient(), batch_seconds=0
    )
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )

    await runtime._handle_message(
        credentials,
        {
            "message_id": "self-1",
            "from_user_id": "bot@im.bot",
            "item_list": [{"type": 1, "text_item": {"text": "echo"}}],
        },
    )
    await runtime._handle_message(
        credentials,
        {
            "message_id": "group-1",
            "from_user_id": "user-1",
            "room_id": "room-1",
            "item_list": [{"type": 1, "text_item": {"text": "group"}}],
        },
    )
    await runtime._handle_message(
        credentials,
        {"message_id": "media-1", "from_user_id": "user-1", "item_list": [{"type": 2}]},
    )
    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count(Run.id))
            .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
            .where(ChannelRunLink.channel_id == channel_id)
        )
        assert count == 0

    await runtime._handle_message(
        credentials,
        {
            "message_id": "dm-1",
            "from_user_id": "user-1",
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            "context_token": "ctx-dm",
        },
    )
    await runtime.flush_all()
    assert worker.wakes == 1
    async with SessionLocal() as db:
        run = await db.scalar(
            select(Run)
            .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
            .where(ChannelRunLink.channel_id == channel_id)
        )
        assert run is not None
        assert run.input == "hello"


@pytest.mark.asyncio
async def test_handle_message_dedups_repeated_text_under_new_ids() -> None:
    channel_id = await _active_channel()
    runtime = WeixinRuntime(
        worker=FakeWorker(), client_factory=lambda **kwargs: FakeClient(), batch_seconds=0
    )
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )
    payload = {
        "from_user_id": "user-1",
        "item_list": [{"type": 1, "text_item": {"text": "same text"}}],
    }
    await runtime._handle_message(credentials, {**payload, "message_id": "dedup-1"})
    await runtime._handle_message(credentials, {**payload, "message_id": "dedup-2"})
    await runtime.flush_all()

    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count(Run.id))
            .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
            .where(ChannelRunLink.channel_id == channel_id)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_handle_command_replies_without_creating_a_run() -> None:
    channel_id = await _active_channel()
    worker = FakeWorker()
    client = FakeClient()
    runtime = WeixinRuntime(worker=worker, client_factory=lambda **kwargs: FakeClient())
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )

    await runtime._handle_message(
        credentials,
        {
            "message_id": "cmd-new-1",
            "from_user_id": "user-cmd",
            "item_list": [{"type": 1, "text_item": {"text": "/new"}}],
            "context_token": "ctx-cmd",
        },
        client,
    )

    assert len(client.sent) == 1
    to_user_id, text, context = client.sent[0]
    assert to_user_id == "user-cmd"
    assert "已开始新会话" in text
    assert context == "ctx-cmd"
    assert worker.wakes == 0
    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count(Run.id))
            .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
            .where(ChannelRunLink.channel_id == channel_id)
        )
        assert count == 0


@pytest.mark.asyncio
async def test_repeated_command_text_is_not_deduped() -> None:
    channel_id = await _active_channel()
    client = FakeClient()
    runtime = WeixinRuntime(worker=FakeWorker(), client_factory=lambda **kwargs: FakeClient())
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )
    payload = {
        "from_user_id": "user-repeat",
        "item_list": [{"type": 1, "text_item": {"text": "/new"}}],
    }
    await runtime._handle_message(credentials, {**payload, "message_id": "cmd-new-a"}, client)
    await runtime._handle_message(credentials, {**payload, "message_id": "cmd-new-b"}, client)

    assert len(client.sent) == 2
    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count(ChannelInbound.external_message_id)).where(
                ChannelInbound.channel_id == channel_id
            )
        )
        assert count == 2


@pytest.mark.asyncio
async def test_stop_command_cancels_active_runs() -> None:
    channel_id = await _active_channel()
    suffix = uuid.uuid4().hex[:10]
    result = await ingest_inbound(
        channel_id=channel_id,
        external_message_id=f"msg-stop-{suffix}",
        external_conversation_id="user-stop",
        conversation_name="Alice",
        text="长任务",
    )
    assert result.run_id is not None

    worker = FakeWorker()
    client = FakeClient()
    runtime = WeixinRuntime(worker=worker, client_factory=lambda **kwargs: FakeClient())
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )
    await runtime._handle_message(
        credentials,
        {
            "message_id": f"cmd-stop-{suffix}",
            "from_user_id": "user-stop",
            "item_list": [{"type": 1, "text_item": {"text": "/stop"}}],
        },
        client,
    )

    assert worker.cancelled == [result.run_id]
    assert client.sent[-1][1] == "已停止 1 个任务。"


@pytest.mark.asyncio
async def test_start_and_stop_channel_tasks() -> None:
    channel_id = await _active_channel()
    client = FakeClient()
    runtime = WeixinRuntime(client_factory=lambda **kwargs: client)

    assert await runtime.start_channel(channel_id) is True
    assert channel_id in runtime.running_channels()
    await runtime.stop_channel(channel_id)
    assert channel_id not in runtime.running_channels()
    assert client.closed is True


@pytest.mark.asyncio
async def test_expired_session_marks_channel_and_stops_polling() -> None:
    channel_id = await _active_channel()
    client = FakeClient(updates=lambda sync_buf: _raise(WeixinSessionExpired("expired")))
    runtime = WeixinRuntime(client_factory=lambda **kwargs: client)

    await runtime.start_channel(channel_id)
    await asyncio.sleep(0.1)
    await runtime.stop()

    async with SessionLocal() as db:
        channel = await db.get(Channel, channel_id)
        assert channel is not None
        assert channel.status == "expired"
        assert channel.last_error
    assert channel_id not in runtime.running_channels()


async def _raise(exc: BaseException):
    raise exc


@pytest.mark.asyncio
async def test_deliver_run_sends_chunks_immediately() -> None:
    channel_id = await _active_channel()
    output = "a" * 4000
    run_id, _ = await _delivery(channel_id, output=output)
    client = FakeClient()
    runtime = WeixinRuntime(client_factory=lambda **kwargs: client)

    await runtime.deliver_run(run_id)

    assert len(client.sent) > 1
    assert "".join(text for _, text, _ in client.sent) == output
    assert all(context == "ctx" for _, _, context in client.sent)
    async with SessionLocal() as db:
        delivery = await db.get(ChannelDelivery, run_id)
        assert delivery is not None
        assert delivery.status == "delivered"
        assert delivery.delivered_at is not None


@pytest.mark.asyncio
async def test_deliver_failure_stays_pending(monkeypatch) -> None:
    monkeypatch.setattr("app.services.weixin_runtime.RETRY_DELAY_SECONDS", 0)
    channel_id = await _active_channel()
    run_id, _ = await _delivery(channel_id)
    client = FakeClient(send_error=WeixinApiError("boom"))
    runtime = WeixinRuntime(client_factory=lambda **kwargs: client)

    await runtime.deliver_run(run_id)

    async with SessionLocal() as db:
        delivery = await db.get(ChannelDelivery, run_id)
        assert delivery is not None
        assert delivery.status == "pending"
        assert delivery.attempts == 1
        assert "boom" in (delivery.last_error or "")


@pytest.mark.asyncio
async def test_sweep_recovers_attempting_with_marker() -> None:
    channel_id = await _active_channel()
    run_id, _ = await _delivery(channel_id, output="recovered reply")
    async with SessionLocal() as db:
        delivery = await db.get(ChannelDelivery, run_id)
        assert delivery is not None
        delivery.status = "attempting"
        await db.commit()

    client = FakeClient()
    runtime = WeixinRuntime(client_factory=lambda **kwargs: client)
    await runtime.sweep_deliveries(channel_id)

    assert client.sent
    assert client.sent[0][1].startswith(RECOVERED_PREFIX)
    assert "recovered reply" in client.sent[0][1]
    async with SessionLocal() as db:
        delivery = await db.get(ChannelDelivery, run_id)
        assert delivery is not None
        assert delivery.status == "delivered"


@pytest.mark.asyncio
async def test_delivery_skips_paused_channel_and_waits() -> None:
    channel_id = await _active_channel()
    run_id, _ = await _delivery(channel_id)
    async with SessionLocal() as db:
        channel = await db.get(Channel, channel_id)
        assert channel is not None
        channel.status = "paused"
        await db.commit()

    client = FakeClient()
    runtime = WeixinRuntime(client_factory=lambda **kwargs: client)
    await runtime.deliver_run(run_id)

    assert client.sent == []
    async with SessionLocal() as db:
        delivery = await db.get(ChannelDelivery, run_id)
        assert delivery is not None
        assert delivery.status == "pending"


@pytest.mark.asyncio
async def test_rapid_messages_are_batched_into_one_run() -> None:
    channel_id = await _active_channel()
    worker = FakeWorker()
    runtime = WeixinRuntime(
        worker=worker,
        client_factory=lambda **kwargs: FakeClient(),
        batch_seconds=0.05,
        batch_max_seconds=1.0,
    )
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )
    for index, text in enumerate(("第一句", "第二句", "第三句")):
        await runtime._handle_message(
            credentials,
            {
                "message_id": f"batch-{index}",
                "from_user_id": "user-batch",
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
        )
    await asyncio.sleep(0.15)

    async with SessionLocal() as db:
        runs = (
            await db.scalars(
                select(Run)
                .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
                .where(ChannelRunLink.channel_id == channel_id)
            )
        ).all()
        assert len(runs) == 1
        assert "第一句" in runs[0].input
        assert "第二句" in runs[0].input
        assert "第三句" in runs[0].input
        inbound_count = await db.scalar(
            select(func.count(ChannelInbound.external_message_id)).where(
                ChannelInbound.channel_id == channel_id
            )
        )
        assert inbound_count == 3
    assert worker.wakes == 1


@pytest.mark.asyncio
async def test_media_and_quote_are_saved_into_the_batched_run() -> None:
    channel_id = await _active_channel()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    client = FakeClient(media=png)
    runtime = WeixinRuntime(worker=FakeWorker(), client_factory=lambda **kwargs: client)
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )
    await runtime._handle_message(
        credentials,
        {
            "message_id": "media-quote-1",
            "from_user_id": "user-media",
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
                        "media": {"full_url": "https://cdn.example.com/a.png"},
                    },
                },
            ],
        },
        client,
    )
    await runtime.flush_all()

    async with SessionLocal() as db:
        run = await db.scalar(
            select(Run)
            .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
            .where(ChannelRunLink.channel_id == channel_id)
        )
        assert run is not None
        assert "附件已上传到工作区" in run.input
        assert "这张图是什么" in run.input
        assert "[引用 Alice]" in run.input
        assert "原来的问题" in run.input
        assert "/uploads/wx-" in run.input
    assert client.downloads == ["https://cdn.example.com/a.png"]


@pytest.mark.asyncio
async def test_voice_message_gets_a_direct_reply_without_a_run() -> None:
    channel_id = await _active_channel()
    client = FakeClient()
    runtime = WeixinRuntime(worker=FakeWorker(), client_factory=lambda **kwargs: client)
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )
    await runtime._handle_message(
        credentials,
        {
            "message_id": "voice-1",
            "from_user_id": "user-voice",
            "item_list": [{"type": 3}],
            "context_token": "ctx-voice",
        },
        client,
    )
    assert client.sent
    assert "暂不支持" in client.sent[-1][1]
    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count(Run.id))
            .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
            .where(ChannelRunLink.channel_id == channel_id)
        )
        assert count == 0
        inbound = await db.scalar(
            select(func.count(ChannelInbound.external_message_id)).where(
                ChannelInbound.channel_id == channel_id
            )
        )
        assert inbound == 1


@pytest.mark.asyncio
async def test_sync_buf_is_saved_only_after_the_packet_flushes(monkeypatch) -> None:
    channel_id = await _active_channel()
    runtime = WeixinRuntime(
        worker=FakeWorker(),
        client_factory=lambda **kwargs: FakeClient(),
        batch_seconds=0.2,
    )
    saved: list[tuple[str, str]] = []

    async def fake_save(channel: str, value: str) -> None:
        saved.append((channel, value))

    monkeypatch.setattr(runtime, "_save_sync_buf", fake_save)
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )
    await runtime._handle_message(
        credentials,
        {
            "message_id": "sync-1",
            "from_user_id": "user-sync",
            "item_list": [{"type": 1, "text_item": {"text": "等等我"}}],
        },
    )
    runtime._pending_sync[channel_id] = "new-buffer"
    await runtime._save_sync_if_idle(channel_id)
    assert saved == []
    await runtime.flush_all()
    assert saved == [(channel_id, "new-buffer")]


@pytest.mark.asyncio
async def test_empty_quote_is_recovered_from_the_delivered_reply() -> None:
    channel_id = await _active_channel()
    contact = f"user-{uuid.uuid4().hex[:10]}"
    origin = await ingest_inbound(
        channel_id=channel_id,
        external_message_id=f"msg-{uuid.uuid4().hex[:10]}",
        external_conversation_id=contact,
        conversation_name="Alice",
        text="今天的日报呢",
        context_token="ctx",
    )
    assert origin.run_id is not None
    await complete_run(
        origin.run_id,
        output="昨天的日报正文",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )
    async with SessionLocal() as db:
        delivery = await db.scalar(
            select(ChannelDelivery).where(ChannelDelivery.run_id == origin.run_id)
        )
        assert delivery is not None
        quoted_at = int(delivery.created_at.timestamp() * 1000)

    runtime = WeixinRuntime(worker=FakeWorker(), client_factory=lambda **kwargs: FakeClient())
    credentials = ChannelCredentials(
        channel_id=channel_id,
        account_id="bot@im.bot",
        token="token",
        base_url="https://ilinkai.weixin.qq.com",
        sync_buf="",
    )
    await runtime._handle_message(
        credentials,
        {
            "message_id": f"quote-{uuid.uuid4().hex[:10]}",
            "from_user_id": contact,
            "item_list": [
                {
                    "type": 1,
                    "text_item": {"text": "这条引用你看到了吗"},
                    "ref_msg": {
                        "message_item": {
                            "type": 0,
                            "create_time_ms": quoted_at,
                            "is_completed": True,
                        }
                    },
                }
            ],
        },
    )
    await runtime.flush_all()

    async with SessionLocal() as db:
        run = await db.scalar(
            select(Run)
            .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
            .where(ChannelRunLink.conversation_id == origin.conversation_id)
            .order_by(Run.created_at.desc())
        )
        assert run is not None
        assert "[引用]" in run.input
        assert "> 昨天的日报正文" in run.input
        assert "这条引用你看到了吗" in run.input
