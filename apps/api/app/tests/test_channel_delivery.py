import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Channel, ChannelDelivery, ChannelRunLink, Run, utcnow
from app.routers.runs import retry_run
from app.services.channel_service import DELIVERY_FAILURE_TEXT, ingest_inbound
from app.services.executor import ExecutorEvent
from app.services.run_service import complete_run
from app.services.worker import RunWorker


class FakeExecutor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def run(self, *, instructions: str, session_id: str, content: str):
        yield ExecutorEvent(
            "run_completed",
            {"output": "hello from wechat", "input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        )


class FailingExecutor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def run(self, *, instructions: str, session_id: str, content: str):
        yield ExecutorEvent(
            "run_failed", {"error": {"type": "Boom", "message": "executor exploded"}}
        )


class FakeRuntime:
    def __init__(self) -> None:
        self.delivered: list[str] = []

    async def deliver_run(self, run_id: str, *, recovered: bool = False) -> None:
        self.delivered.append(run_id)


async def _channel_run(text: str = "hi") -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    async with SessionLocal() as db:
        channel = Channel(
            agent_id="default",
            type="wechat",
            name=f"delivery-{suffix}",
            status="active",
            account_id="bot@im.bot",
            bot_token="token",
        )
        db.add(channel)
        await db.commit()
        await db.refresh(channel)
        channel_id = channel.id
    result = await ingest_inbound(
        channel_id=channel_id,
        external_message_id=f"msg-{suffix}",
        external_conversation_id=f"user-{suffix}",
        conversation_name="Alice",
        text=text,
        context_token="ctx",
    )
    assert result.run_id is not None
    return channel_id, result.run_id


@pytest.mark.asyncio
async def test_completed_channel_run_records_delivery_and_sends_immediately(monkeypatch) -> None:
    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", FakeExecutor)
    channel_id, run_id = await _channel_run()
    runtime = FakeRuntime()
    worker = RunWorker(weixin_runtime=runtime)

    await worker.execute_run(run_id)

    assert runtime.delivered == [run_id]
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        assert run is not None and run.status == "completed"
        delivery = await db.get(ChannelDelivery, run_id)
        assert delivery is not None
        assert delivery.status == "pending"  # FakeRuntime does not mark it sent
        assert delivery.content == "hello from wechat"
        assert delivery.channel_id == channel_id
        assert await db.get(ChannelRunLink, run_id) is not None


@pytest.mark.asyncio
async def test_failed_channel_run_records_generic_error_reply(monkeypatch) -> None:
    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", FailingExecutor)
    _, run_id = await _channel_run()
    runtime = FakeRuntime()
    worker = RunWorker(weixin_runtime=runtime)

    await worker.execute_run(run_id)

    assert runtime.delivered == [run_id]
    async with SessionLocal() as db:
        delivery = await db.get(ChannelDelivery, run_id)
        assert delivery is not None
        assert delivery.content == DELIVERY_FAILURE_TEXT
        assert "executor exploded" not in delivery.content


@pytest.mark.asyncio
async def test_cancelled_channel_run_has_no_delivery() -> None:
    _, run_id = await _channel_run()
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        assert run is not None
        run.cancel_requested = True
        run.status = "cancelled"
        run.finished_at = utcnow()
        await db.commit()

    runtime = FakeRuntime()
    worker = RunWorker(weixin_runtime=runtime)
    await worker.execute_run(run_id)

    assert runtime.delivered == []
    async with SessionLocal() as db:
        assert await db.get(ChannelDelivery, run_id) is None


@pytest.mark.asyncio
async def test_complete_run_does_not_duplicate_delivery() -> None:
    _, run_id = await _channel_run()
    async with SessionLocal() as db:
        await complete_run(
            run_id, output="once", input_tokens=0, output_tokens=0, total_tokens=0
        )
        await complete_run(
            run_id, output="twice", input_tokens=0, output_tokens=0, total_tokens=0
        )
        count = await db.scalar(
            select(func.count(ChannelDelivery.run_id)).where(ChannelDelivery.run_id == run_id)
        )
        assert count == 1
        delivery = await db.get(ChannelDelivery, run_id)
        assert delivery is not None and delivery.content == "once"


@pytest.mark.asyncio
async def test_retry_copies_channel_link_and_replies(monkeypatch) -> None:
    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", FailingExecutor)
    _, original_run_id = await _channel_run()
    await RunWorker(weixin_runtime=FakeRuntime()).execute_run(original_run_id)

    fake_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(worker=None))
    )
    async with SessionLocal() as db:
        result = await retry_run(original_run_id, fake_request, db)
    retry_run_id = result["run_id"]
    async with SessionLocal() as db:
        assert await db.get(ChannelRunLink, retry_run_id) is not None

    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", FakeExecutor)
    runtime = FakeRuntime()
    await RunWorker(weixin_runtime=runtime).execute_run(retry_run_id)

    assert runtime.delivered == [retry_run_id]
    async with SessionLocal() as db:
        retry_delivery = await db.get(ChannelDelivery, retry_run_id)
        assert retry_delivery is not None
        assert retry_delivery.content == "hello from wechat"
