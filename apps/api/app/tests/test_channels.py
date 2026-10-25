import os
import sqlite3

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    Channel,
    ChannelConversation,
    ChannelInbound,
    ChannelRunLink,
    Message,
    Run,
    Session,
    utcnow,
)
from app.services.channel_service import ChannelUnavailable, ingest_inbound
from app.routers.sessions import _session_out


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


def _db_path() -> str:
    return os.environ["APP_DATABASE_URL"].split("///", 1)[1]


def _sqlite_execute(statement: str, params: tuple = ()) -> None:
    with sqlite3.connect(_db_path()) as connection:
        connection.execute(statement, params)
        connection.commit()


def _sqlite_scalar(statement: str, params: tuple = ()) -> object:
    with sqlite3.connect(_db_path()) as connection:
        row = connection.execute(statement, params).fetchone()
    return row[0] if row else None


async def _create_active_channel(agent_id: str = "default") -> str:
    async with SessionLocal() as db:
        channel = Channel(
            agent_id=agent_id,
            type="wechat",
            name=f"test-wechat-{utcnow().timestamp()}",
            status="active",
            account_id="bot-account@im.bot",
            bot_token="secret-bot-token",
            base_url="https://ilink.example",
        )
        db.add(channel)
        await db.commit()
        await db.refresh(channel)
        return channel.id


async def _ingest(channel_id: str, message_id: str, *, external_id: str = "user-1", text: str = "你好"):
    return await ingest_inbound(
        channel_id=channel_id,
        external_message_id=message_id,
        external_conversation_id=external_id,
        conversation_name="Alice",
        sender_name="Alice",
        text=text,
        context_token=f"ctx-{message_id}",
    )


def test_channel_create_and_list_hides_secret(client) -> None:
    response = client.post("/api/channels", json={"agent_id": "default", "name": "我的微信"})
    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "pairing"
    assert created["has_credentials"] is False
    assert "bot_token" not in created

    _sqlite_execute(
        "UPDATE channels SET account_id = ?, bot_token = ? WHERE id = ?",
        ("acct@im.bot", "top-secret-token", created["id"]),
    )
    listed = client.get("/api/channels", params={"agent_id": "default"})
    assert listed.status_code == 200
    body = listed.text
    assert "top-secret-token" not in body
    row = next(item for item in listed.json() if item["id"] == created["id"])
    assert row["has_credentials"] is True


@pytest.mark.asyncio
async def test_channel_out_never_exposes_token() -> None:
    from app.routers.channels import _channel_out

    async with SessionLocal() as db:
        channel = Channel(
            agent_id="default",
            type="wechat",
            name="token-hiding",
            status="active",
            account_id="acct@im.bot",
            bot_token="top-secret-token",
        )
        db.add(channel)
        await db.commit()
        await db.refresh(channel)
        payload = (await _channel_out(db, channel)).model_dump()
        assert payload["has_credentials"] is True
        assert "bot_token" not in payload
        assert "top-secret-token" not in str(payload)


def test_channel_activation_requires_login(client) -> None:
    created = client.post(
        "/api/channels", json={"agent_id": "default", "name": "待登录"}
    ).json()
    response = client.put(f"/api/channels/{created['id']}", json={"status": "active"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_inbound_creates_session_message_and_run() -> None:
    channel_id = await _create_active_channel()
    result = await _ingest(channel_id, "msg-1")

    assert result.duplicate is False
    assert result.run_id and result.session_id and result.message_id
    async with SessionLocal() as db:
        run = await db.get(Run, result.run_id)
        assert run is not None
        assert run.source == "wechat"
        assert run.status == "queued"
        session = await db.get(Session, result.session_id)
        assert session is not None
        assert session.title == "微信 · Alice"
        message = await db.get(Message, result.message_id)
        assert message is not None
        assert message.role == "user"
        conversation = await db.scalar(
            select(ChannelConversation).where(
                ChannelConversation.session_id == result.session_id
            )
        )
        assert conversation is not None
        assert conversation.context_token == "ctx-msg-1"
        assert await db.get(ChannelRunLink, result.run_id) is not None
        inbound = await db.scalar(
            select(ChannelInbound).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id == "msg-1",
            )
        )
        assert inbound is not None
        assert inbound.run_id == result.run_id


@pytest.mark.asyncio
async def test_inbound_is_idempotent() -> None:
    channel_id = await _create_active_channel()
    first = await _ingest(channel_id, "msg-dup")
    second = await _ingest(channel_id, "msg-dup")

    assert second.duplicate is True
    assert second.run_id == first.run_id
    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count(Run.id)).where(Run.session_id == first.session_id)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_inbound_maps_each_contact_to_its_own_session() -> None:
    channel_id = await _create_active_channel()
    alice = await _ingest(channel_id, "msg-a", external_id="alice")
    bob = await _ingest(channel_id, "msg-b", external_id="bob")

    assert alice.session_id != bob.session_id
    async with SessionLocal() as db:
        sessions = (
            await db.scalars(
                select(Session).where(Session.id.in_([alice.session_id, bob.session_id]))
            )
        ).all()
        assert len(sessions) == 2


@pytest.mark.asyncio
async def test_inbound_queues_while_an_active_run_exists(client) -> None:
    channel_id = await _create_active_channel()
    first = await _ingest(channel_id, "msg-q1")
    second = await _ingest(channel_id, "msg-q2")

    async with SessionLocal() as db:
        runs = (
            await db.scalars(
                select(Run).where(Run.session_id == first.session_id).order_by(Run.created_at)
            )
        ).all()
        assert [run.status for run in runs] == ["queued", "queued"]
        assert {run.id for run in runs} == {first.run_id, second.run_id}

    # The web composer still refuses to add a third run to the same Session.
    response = client.post(
        "/api/chat/messages",
        json={"session_id": first.session_id, "content": "from web"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_inbound_rejects_paused_channel() -> None:
    channel_id = await _create_active_channel()
    async with SessionLocal() as db:
        channel = await db.get(Channel, channel_id)
        assert channel is not None
        channel.status = "paused"
        await db.commit()

    with pytest.raises(ChannelUnavailable):
        await _ingest(channel_id, "msg-paused")


@pytest.mark.asyncio
async def test_session_list_and_active_run_endpoint_expose_queued_run(client) -> None:
    channel_id = await _create_active_channel()
    result = await _ingest(channel_id, "msg-active-run", external_id="active-user")

    sessions = client.get("/api/agents/default/sessions")
    assert sessions.status_code == 200
    row = next(item for item in sessions.json() if item["id"] == result.session_id)
    assert row["active_run_id"] == result.run_id
    assert row["active_run_status"] == "queued"

    active = client.get(f"/api/sessions/{result.session_id}/active-run")
    assert active.status_code == 200
    assert active.json()["id"] == result.run_id
    assert active.json()["source"] == "wechat"


@pytest.mark.asyncio
async def test_delete_channel_keeps_session_but_removes_binding(client) -> None:
    channel_id = await _create_active_channel()
    result = await _ingest(channel_id, "msg-del-channel")

    response = client.delete(f"/api/channels/{channel_id}")
    assert response.status_code == 200

    async with SessionLocal() as db:
        assert await db.get(Channel, channel_id) is None
        assert await db.get(Session, result.session_id) is not None
        conversation = await db.scalar(
            select(ChannelConversation).where(
                ChannelConversation.session_id == result.session_id
            )
        )
        assert conversation is None
        assert await db.get(ChannelRunLink, result.run_id) is None


@pytest.mark.asyncio
async def test_delete_session_unlinks_conversation(client) -> None:
    channel_id = await _create_active_channel()
    result = await _ingest(channel_id, "msg-del-session")
    async with SessionLocal() as db:
        run = await db.get(Run, result.run_id)
        assert run is not None
        run.status = "completed"
        run.finished_at = utcnow()
        await db.commit()

    response = client.delete(f"/api/sessions/{result.session_id}")
    assert response.status_code == 200

    async with SessionLocal() as db:
        conversation = await db.scalar(
            select(ChannelConversation).where(
                ChannelConversation.session_id == result.session_id
            )
        )
        assert conversation is None


def test_agent_permanent_delete_cascades_channels(client) -> None:
    agent = client.post(
        "/api/agents",
        json={"name": "Channel Agent", "instructions": "test"},
    ).json()
    channel = client.post(
        "/api/channels", json={"agent_id": agent["id"], "name": "agent-wechat"}
    ).json()

    response = client.delete(f"/api/agents/{agent['id']}/permanent")
    assert response.status_code == 200
    assert _sqlite_scalar("SELECT COUNT(*) FROM channels WHERE id = ?", (channel["id"],)) == 0
    assert _sqlite_scalar("SELECT COUNT(*) FROM agents WHERE id = ?", (agent["id"],)) == 0


class _FakeLoginClient:
    def __init__(self, status: dict) -> None:
        self.status = status

    async def fetch_qr(self):
        return {
            "qrcode": "qr-token",
            "qrcode_img_content": "https://example.com/weixin/scan",
        }

    async def qr_status(self, qrcode: str, *, base_url: str | None = None):
        return self.status

    async def aclose(self) -> None:
        return None


class _FakeRuntime:
    def __init__(self, status: dict) -> None:
        self.status = status
        self.login: dict[str, dict] = {}
        self.started: list[str] = []
        self.swept: list[str | None] = []

    def new_client(self, **kwargs):
        return _FakeLoginClient(self.status)

    def set_login(self, channel_id: str, **kwargs) -> None:
        self.login[channel_id] = kwargs

    def get_login(self, channel_id: str):
        return self.login.get(channel_id)

    def clear_login(self, channel_id: str) -> None:
        self.login.pop(channel_id, None)

    async def start_channel(self, channel_id: str) -> bool:
        self.started.append(channel_id)
        return True

    async def stop_channel(self, channel_id: str) -> None:
        return None

    async def sweep_deliveries(self, channel_id: str | None = None) -> None:
        self.swept.append(channel_id)


def test_wechat_qr_login_saves_credentials_and_hides_token(client) -> None:
    created = client.post(
        "/api/channels", json={"agent_id": "default", "name": "扫码测试"}
    ).json()
    fake = _FakeRuntime(
        {
            "status": "confirmed",
            "ilink_bot_id": "bot-account@im.bot",
            "bot_token": "confirmed-token",
            "baseurl": "https://ilinkai.weixin.qq.com",
            "ilink_user_id": "user-id",
        }
    )
    client.app.state.weixin = fake

    login = client.post(f"/api/channels/{created['id']}/wechat/login")
    assert login.status_code == 200
    body = login.json()
    assert body["qrcode"] == "qr-token"
    assert body["scan_url"] == "https://example.com/weixin/scan"
    assert "<svg" in body["qr_svg"]

    status = client.get(
        f"/api/channels/{created['id']}/wechat/login/status",
        params={"qrcode": "qr-token"},
    )
    assert status.status_code == 200
    assert status.json() == {"status": "confirmed", "account_id": "bot-account@im.bot"}
    assert fake.started == [created["id"]]
    assert fake.swept == [created["id"]]

    assert _sqlite_scalar("SELECT status FROM channels WHERE id = ?", (created["id"],)) == "active"
    assert (
        _sqlite_scalar("SELECT bot_token FROM channels WHERE id = ?", (created["id"],))
        == "confirmed-token"
    )
    listed = client.get("/api/channels", params={"agent_id": "default"})
    assert "confirmed-token" not in listed.text


@pytest.mark.asyncio
async def test_session_summary_exposes_wechat_channel(client) -> None:  # noqa: ARG001
    channel_id = await _create_active_channel("default")
    result = await _ingest(channel_id, "session-channel-msg")
    assert result.session_id is not None
    async with SessionLocal() as db:
        session = await db.get(Session, result.session_id)
        assert session is not None
        summary = await _session_out(db, session)
    assert summary.channel_type == "wechat"
    assert summary.channel_name is not None
    assert summary.channel_name.startswith("test-wechat-")
