import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import Channel, ChannelDelivery, ChannelRunLink, Run, utcnow
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

    def wake(self) -> None:
        self.wakes += 1


class FakeClient:
    def __init__(self, *, updates=None, send_error=None, **kwargs) -> None:
        self.updates = updates
        self.send_error = send_error
        self.sent: list[tuple[str, str, str | None]] = []
        self.closed = False

    async def get_updates(self, sync_buf: str):
        if callable(self.updates):
            return await self.updates(sync_buf)
        if self.updates is not None:
            return self.updates
        await asyncio.sleep(3600)

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
    runtime = WeixinRuntime(worker=FakeWorker(), client_factory=lambda **kwargs: FakeClient())
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

    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count(Run.id))
            .join(ChannelRunLink, ChannelRunLink.run_id == Run.id)
            .where(ChannelRunLink.channel_id == channel_id)
        )
        assert count == 1


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
