from __future__ import annotations

import pytest

from app.services import channel_images
from app.services.settings_store import (
    ChannelSection,
    ConversationSection,
    PlatformSettings,
)

SESSION_ID = "11111111-1111-1111-1111-111111111111"


class FakeStore:
    def __init__(self, settings: PlatformSettings) -> None:
        self._settings = settings

    async def get(self) -> PlatformSettings:
        return self._settings


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, str | None]] = []
        self.closed = False

    async def send_image(self, *, to_user_id: str, image: bytes, context_token=None):
        self.sent.append((to_user_id, image, context_token))

    async def aclose(self) -> None:
        self.closed = True


def _settings(*, status: str = "active") -> PlatformSettings:
    channel = ChannelSection(
        id="ch1",
        agent_id="default",
        name="Bot",
        status=status,
        account_id="acct",
        bot_token="token",
    )
    conversation = ConversationSection(
        session_id=SESSION_ID,
        channel_id="ch1",
        display_name="张三",
        context_token="ctx",
    )
    return PlatformSettings(
        channels=[channel],
        channel_conversations={"ch1:wx-1": conversation},
    )


@pytest.mark.asyncio
async def test_send_session_image_uses_bound_conversation(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(channel_images, "_build_client", lambda channel: client)
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")

    ok = await channel_images.send_session_image(
        SESSION_ID, image, store=FakeStore(_settings())
    )

    assert ok is True
    assert client.sent == [("wx-1", b"png", "ctx")]
    assert client.closed is True


@pytest.mark.asyncio
async def test_send_session_image_skips_without_binding(tmp_path, monkeypatch):
    built: list[ChannelSection] = []

    def factory(channel):
        built.append(channel)
        return FakeClient()

    monkeypatch.setattr(channel_images, "_build_client", factory)
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")
    settings = PlatformSettings(channels=_settings().channels)

    ok = await channel_images.send_session_image(
        "22222222-2222-2222-2222-222222222222", image, store=FakeStore(settings)
    )

    assert ok is False
    assert built == []


@pytest.mark.asyncio
async def test_send_session_image_skips_inactive_channel(tmp_path, monkeypatch):
    built: list[ChannelSection] = []

    def factory(channel):
        built.append(channel)
        return FakeClient()

    monkeypatch.setattr(channel_images, "_build_client", factory)
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")

    ok = await channel_images.send_session_image(
        SESSION_ID, image, store=FakeStore(_settings(status="paused"))
    )

    assert ok is False
    assert built == []


class FailingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def send_image(self, *, to_user_id: str, image: bytes, context_token=None):
        self.attempts += 1
        raise RuntimeError("send exploded")


@pytest.mark.asyncio
async def test_send_session_image_skips_oversized_file(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(channel_images, "_build_client", lambda channel: client)
    monkeypatch.setattr(channel_images.settings, "browser_screenshot_max_bytes", 2)
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")

    ok = await channel_images.send_session_image(
        SESSION_ID, image, store=FakeStore(_settings())
    )

    assert ok is False
    assert client.sent == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_send_session_image_skips_unreadable_file(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(channel_images, "_build_client", lambda channel: client)

    ok = await channel_images.send_session_image(
        SESSION_ID, tmp_path / "missing.png", store=FakeStore(_settings())
    )

    assert ok is False
    assert client.sent == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_send_session_image_closes_client_after_send_failure(
    tmp_path, monkeypatch
):
    client = FailingClient()
    monkeypatch.setattr(channel_images, "_build_client", lambda channel: client)
    monkeypatch.setattr(channel_images, "RETRY_DELAY_SECONDS", 0)
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")

    ok = await channel_images.send_session_image(
        SESSION_ID, image, store=FakeStore(_settings())
    )

    assert ok is False
    assert client.attempts == channel_images.SEND_ATTEMPTS
    assert client.closed is True


@pytest.mark.asyncio
async def test_send_session_image_rejects_mismatched_binding_key(
    tmp_path, monkeypatch
):
    built: list[ChannelSection] = []

    def factory(channel):
        built.append(channel)
        return FakeClient()

    monkeypatch.setattr(channel_images, "_build_client", factory)
    settings = _settings()
    conversation = settings.channel_conversations.pop("ch1:wx-1")
    settings.channel_conversations["other:wx-1"] = conversation
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")

    ok = await channel_images.send_session_image(
        SESSION_ID, image, store=FakeStore(settings)
    )

    assert ok is False
    assert built == []
