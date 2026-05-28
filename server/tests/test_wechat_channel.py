from __future__ import annotations

from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.channel_routes import create_channel_router
from server.adapter.dependencies import AppContainer
from server.app.auth_service import AuthService
from server.app.wechat_channel_service import WechatChannelService, WechatChannelStatus
from server.domain.auth import AuthToken
from server.infrastructure.session import SessionCodec


class FakeWechatService:
    def __init__(self) -> None:
        self._status = WechatChannelStatus(
            running=False,
            login_state="stopped",
            qrcode_url="",
            qrcode_data_url="",
            qrcode_status="",
            user="",
            error="",
            logs=(),
        )
        self.started = False
        self.stopped = False

    async def status(self) -> WechatChannelStatus:
        return self._status

    async def start(self) -> WechatChannelStatus:
        self.started = True
        self._status = WechatChannelStatus(
            running=True,
            login_state="starting",
            qrcode_url="",
            qrcode_data_url="",
            qrcode_status="",
            user="",
            error="",
            logs=(),
        )
        return self._status

    async def stop(self) -> WechatChannelStatus:
        self.stopped = True
        self._status = WechatChannelStatus(
            running=False,
            login_state="stopped",
            qrcode_url="",
            qrcode_data_url="",
            qrcode_status="",
            user="",
            error="",
            logs=(),
        )
        return self._status


def make_client(fake_service: FakeWechatService | None = None) -> TestClient:
    from server.adapter.auth_routes import create_auth_router

    svc = fake_service or FakeWechatService()
    token = AuthToken("test-token")
    container = AppContainer(
        auth_service=AuthService(token),
        config_file_service=MagicMock(),
        proxy_service=MagicMock(),
        system_log_service=MagicMock(),
        system_update_service=MagicMock(),
        session_codec=SessionCodec("test-token"),
        wechat_channel_service=svc,
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_channel_router(container))
    return TestClient(app)


def auth_headers(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"token": "test-token"})
    assert resp.status_code == 200, f"login failed: {resp.json()}"
    cookie = resp.headers.get("set-cookie", "")
    return {"Cookie": cookie.split(";")[0]} if cookie else {}


class TestWechatChannelRoutes:
    def test_requires_auth(self) -> None:
        client = make_client()
        resp = client.get("/api/channels/wechat/status")
        assert resp.status_code == 401

    def test_start_and_stop(self) -> None:
        fake = FakeWechatService()
        client = make_client(fake)
        headers = auth_headers(client)
        resp = client.post("/api/channels/wechat/start", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["wechat"]["login_state"] == "starting"
        assert fake.started is True
        resp = client.post("/api/channels/wechat/stop", headers=headers)
        assert resp.status_code == 200
        assert fake.stopped is True

    def test_status_returns_fields(self) -> None:
        fake = FakeWechatService()
        client = make_client(fake)
        headers = auth_headers(client)
        resp = client.get("/api/channels/wechat/status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["wechat"]
        for field in (
            "running", "login_state", "qrcode_url", "qrcode_data_url",
            "qrcode_status", "user", "error", "logs",
        ):
            assert field in data


class TestWechatChannelService:
    def test_initial_status_stopped(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        assert svc._login_state == "stopped"
        assert svc._user == ""

    def test_message_allowed_empty_whitelist(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        event: dict[str, Any] = {"talker_name": "test_user", "room_topic": ""}
        assert svc._message_allowed(event) is True

    def test_message_allowed_contacts(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        event: dict[str, Any] = {"talker_name": "Alice", "room_topic": ""}
        assert svc._message_allowed(event) is True

    def test_message_allowed_room_whitelist(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        event: dict[str, Any] = {"talker_name": "Bob", "room_topic": "TestRoom"}
        assert svc._message_allowed(event) is True

    def test_snapshot_locked(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        snapshot = svc._snapshot_locked()
        assert snapshot.running is False
        assert snapshot.login_state == "stopped"
        assert isinstance(snapshot.logs, tuple)

    def test_last_error_locked_returns_latest(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        svc._logs.append({"type": "error", "error": "first error"})
        svc._logs.append({"type": "info", "message": "hello"})
        svc._logs.append({"type": "error", "error": "latest error"})
        assert svc._last_error_locked() == "latest error"


class TestILinkClient:
    def test_generate_qrcode_data_url(self) -> None:
        from server.infrastructure.ilink_client import generate_qrcode_data_url
        url = generate_qrcode_data_url("test_qrcode_string")
        assert url.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_start_creates_task(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        status = await svc.start()
        assert status.login_state == "connecting"
        await svc.stop()
        assert svc._task is None

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        await svc.start()
        await svc.stop()
        assert svc._login_state == "stopped"
        assert svc._task is None
