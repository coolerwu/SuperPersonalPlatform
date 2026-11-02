from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Agent,
    Channel,
    ChannelConversation,
    ChannelDelivery,
    ChannelRunLink,
    CronJob,
    Run,
    Session,
    utcnow,
)
from app.services.cron_service import run_due_jobs
from app.services.run_service import complete_run


async def _make_channel_conversation(
    agent_id: str = "default", *, name: str = "Alice", status: str = "active"
) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        channel = Channel(
            agent_id=agent_id,
            type="wechat",
            name=f"cron-channel-{suffix}",
            status=status,
            account_id=f"bot-{suffix}",
            bot_token="test-token",
            base_url="https://ilink.example",
        )
        db.add(channel)
        await db.flush()
        session = Session(agent_id=agent_id, title=f"微信 · {name}")
        db.add(session)
        await db.flush()
        conversation = ChannelConversation(
            channel_id=channel.id,
            external_id=f"wx-{suffix}",
            display_name=name,
            session_id=session.id,
            context_token=f"ctx-{suffix}",
        )
        db.add(conversation)
        await db.commit()
        await db.refresh(channel)
        await db.refresh(conversation)
        return channel.id, conversation.id, session.id


def _create_job(client, **overrides):
    payload = {
        "agent_id": "default",
        "name": f"push-{uuid.uuid4().hex[:8]}",
        "prompt": "把结果发到微信",
        "schedule": "0 8 * * *",
        "timezone": "Asia/Shanghai",
        "enabled": True,
    }
    payload.update(overrides)
    return client.post("/api/cron-jobs", json=payload)


async def _fire(job_id: str) -> Run | None:
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        job.next_run_at = utcnow() - timedelta(minutes=1)
        await db.commit()
    await run_due_jobs(utcnow())
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        if not job.last_run_id:
            return None
        return await db.get(Run, job.last_run_id)


async def test_channel_conversation_list_excludes_tokens(client) -> None:
    channel_id, conversation_id, _ = await _make_channel_conversation()

    response = client.get(f"/api/channels/{channel_id}/conversations")
    assert response.status_code == 200, response.text
    rows = response.json()
    row = next(item for item in rows if item["id"] == conversation_id)
    assert row["display_name"] == "Alice"
    assert row["session_title"].startswith("微信")
    assert "context_token" not in row
    assert "external_id" not in row


async def test_job_accepts_explicit_push_target(client) -> None:
    channel_id, conversation_id, _ = await _make_channel_conversation()
    response = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=conversation_id
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["push_channel_id"] == channel_id
    assert job["push_conversation_id"] == conversation_id
    assert job["push_channel_name"].startswith("cron-channel-")
    assert job["push_conversation_name"] == "Alice"

    listed = client.get("/api/cron-jobs?agent_id=default").json()
    row = next(item for item in listed if item["id"] == job["id"])
    assert row["push_conversation_name"] == "Alice"


async def test_push_target_validation(client) -> None:
    channel_id, conversation_id, _ = await _make_channel_conversation()
    other_channel, other_conversation, _ = await _make_channel_conversation()

    assert (
        _create_job(client, push_channel_id=channel_id).status_code == 400
    )
    assert (
        _create_job(client, push_conversation_id=conversation_id).status_code == 400
    )
    assert (
        _create_job(
            client,
            push_channel_id=channel_id,
            push_conversation_id=other_conversation,
        ).status_code
        == 400
    )

    other_agent = client.post(
        "/api/agents", json={"name": f"PushAgent-{uuid.uuid4().hex[:6]}"}
    ).json()
    async with SessionLocal() as db:
        foreign_channel = Channel(
            agent_id=other_agent["id"],
            type="wechat",
            name="foreign",
            status="active",
        )
        db.add(foreign_channel)
        await db.flush()
        foreign_session = Session(agent_id=other_agent["id"], title="微信 · Foreign")
        db.add(foreign_session)
        await db.flush()
        foreign_conversation = ChannelConversation(
            channel_id=foreign_channel.id,
            external_id=f"foreign-{uuid.uuid4().hex[:8]}",
            display_name="Foreign",
            session_id=foreign_session.id,
        )
        db.add(foreign_conversation)
        await db.commit()
        foreign_channel_id = foreign_channel.id
        foreign_conversation_id = foreign_conversation.id
    assert (
        _create_job(
            client,
            push_channel_id=foreign_channel_id,
            push_conversation_id=foreign_conversation_id,
        ).status_code
        == 400
    )
    client.delete(f"/api/agents/{other_agent['id']}/permanent")


async def test_trigger_links_and_delivers_to_target(client) -> None:
    channel_id, conversation_id, _ = await _make_channel_conversation()
    job = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=conversation_id
    ).json()

    run = await _fire(job["id"])
    assert run is not None
    async with SessionLocal() as db:
        link = await db.get(ChannelRunLink, run.id)
        assert link is not None
        assert link.channel_id == channel_id
        assert link.conversation_id == conversation_id

    await complete_run(
        run.id,
        output="定时任务的结果",
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
    )
    async with SessionLocal() as db:
        delivery = await db.get(ChannelDelivery, run.id)
        assert delivery is not None
        assert delivery.channel_id == channel_id
        assert delivery.conversation_id == conversation_id
        assert delivery.content == "定时任务的结果"
        assert delivery.status == "pending"


async def test_no_target_means_no_delivery(client) -> None:
    job = _create_job(client).json()
    run = await _fire(job["id"])
    assert run is not None
    await complete_run(
        run.id, output="只留在网页", input_tokens=0, output_tokens=0, total_tokens=0
    )
    async with SessionLocal() as db:
        assert await db.get(ChannelRunLink, run.id) is None
        assert await db.get(ChannelDelivery, run.id) is None


async def test_manual_run_uses_push_target(client) -> None:
    channel_id, conversation_id, _ = await _make_channel_conversation()
    job = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=conversation_id
    ).json()
    response = client.post(f"/api/cron-jobs/{job['id']}/run")
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    async with SessionLocal() as db:
        link = await db.get(ChannelRunLink, run_id)
        assert link is not None
        assert link.conversation_id == conversation_id


async def test_deleting_channel_clears_job_target(client) -> None:
    channel_id, conversation_id, _ = await _make_channel_conversation()
    job = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=conversation_id
    ).json()
    assert client.delete(f"/api/channels/{channel_id}").status_code == 200

    listed = client.get("/api/cron-jobs?agent_id=default").json()
    row = next(item for item in listed if item["id"] == job["id"])
    assert row["push_channel_id"] is None
    assert row["push_conversation_id"] is None


async def test_deleting_conversation_session_clears_job_target(client) -> None:
    channel_id, conversation_id, session_id = await _make_channel_conversation()
    job = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=conversation_id
    ).json()
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200

    listed = client.get("/api/cron-jobs?agent_id=default").json()
    row = next(item for item in listed if item["id"] == job["id"])
    assert row["push_channel_id"] is None
    assert row["push_conversation_id"] is None


async def test_deleting_cron_session_cleans_links_and_deliveries(client) -> None:
    channel_id, conversation_id, _ = await _make_channel_conversation()
    job = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=conversation_id
    ).json()
    run = await _fire(job["id"])
    assert run is not None
    await complete_run(
        run.id, output="待发送", input_tokens=0, output_tokens=0, total_tokens=0
    )
    async with SessionLocal() as db:
        assert await db.get(ChannelDelivery, run.id) is not None

    assert client.delete(f"/api/sessions/{job['session_id']}").status_code == 200
    async with SessionLocal() as db:
        assert await db.get(ChannelRunLink, run.id) is None
        assert await db.get(ChannelDelivery, run.id) is None
        assert await db.get(Session, job["session_id"]) is None


async def test_update_push_target_requires_both_fields(client) -> None:
    channel_id, conversation_id, _ = await _make_channel_conversation()
    job = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=conversation_id
    ).json()

    partial = client.put(
        f"/api/cron-jobs/{job['id']}", json={"push_channel_id": channel_id}
    )
    assert partial.status_code == 400
    partial = client.put(
        f"/api/cron-jobs/{job['id']}",
        json={"push_conversation_id": conversation_id},
    )
    assert partial.status_code == 400

    cleared = client.put(
        f"/api/cron-jobs/{job['id']}",
        json={"push_channel_id": "", "push_conversation_id": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["push_channel_id"] is None
    assert cleared.json()["push_conversation_id"] is None


async def test_failed_run_pushes_failure_text(client) -> None:
    from app.services.channel_service import DELIVERY_FAILURE_TEXT
    from app.services.run_service import fail_run

    channel_id, conversation_id, _ = await _make_channel_conversation()
    job = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=conversation_id
    ).json()
    run = await _fire(job["id"])
    assert run is not None
    await fail_run(run.id, "TestError", "boom")
    async with SessionLocal() as db:
        delivery = await db.get(ChannelDelivery, run.id)
        assert delivery is not None
        assert delivery.content == DELIVERY_FAILURE_TEXT
