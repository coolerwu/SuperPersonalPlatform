from __future__ import annotations

import uuid

import pytest

from app.db import SessionLocal, init_db
from app.models import Agent, Channel, Provider
from app.services import channel_images
from app.services.channel_service import ingest_inbound


class FakeImageClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, str | None]] = []
        self.closed = False

    async def send_image(
        self,
        *,
        to_user_id: str,
        image: bytes,
        context_token: str | None = None,
        client_id: str | None = None,
    ) -> dict[str, int]:
        self.sent.append((to_user_id, image, context_token))
        return {"ret": 0}

    async def aclose(self) -> None:
        self.closed = True


async def _channel_run(text: str = "hi") -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:10]
    async with SessionLocal() as db:
        if await db.get(Provider, "default") is None:
            db.add(Provider(id="default", name="default"))
        if await db.get(Agent, "default") is None:
            db.add(Agent(id="default", name="Assistant"))
        await db.commit()
        channel = Channel(
            agent_id="default",
            type="wechat",
            name=f"image-{suffix}",
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
        context_token="ctx-image",
    )
    assert result.run_id is not None
    return channel_id, result.run_id, f"user-{suffix}"


@pytest.mark.asyncio
async def test_send_run_image_uses_channel_link(tmp_path, monkeypatch) -> None:
    await init_db()
    _, run_id, external_id = await _channel_run()
    fake = FakeImageClient()
    monkeypatch.setattr(channel_images, "_build_client", lambda channel: fake)

    path = tmp_path / "qr.png"
    data = b"\x89PNG\r\n\x1a\nqr-bytes"
    path.write_bytes(data)

    assert await channel_images.send_run_image(run_id, path) is True
    assert fake.sent == [(external_id, data, "ctx-image")]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_send_run_image_skips_runs_without_channel_link(tmp_path) -> None:
    path = tmp_path / "qr.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nqr-bytes")

    assert await channel_images.send_run_image("missing-run", path) is False


@pytest.mark.asyncio
async def test_send_run_image_skips_oversized_images(tmp_path, monkeypatch) -> None:
    from app.config import settings

    await init_db()
    _, run_id, _ = await _channel_run()
    fake = FakeImageClient()
    monkeypatch.setattr(channel_images, "_build_client", lambda channel: fake)
    monkeypatch.setattr(settings, "browser_screenshot_max_bytes", 4)

    path = tmp_path / "big.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nqr-bytes")

    assert await channel_images.send_run_image(run_id, path) is False
    assert fake.sent == []
