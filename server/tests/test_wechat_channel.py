from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.channel_routes import create_channel_router
from server.adapter.dependencies import AppContainer
from server.app.auth_service import AuthService
from server.app.chat_session_service import ChatSessionService
from server.app.wechat_channel_service import WechatChannelService, WechatChannelStatus
from server.app.wechat_channel_manager import WechatChannelManager
from server.domain.auth import AuthToken
from server.domain.sessions import ChatMessageData
from server.infrastructure.session import SessionCodec


# ---------------------------------------------------------------------------
# Fake single-account service (kept for legacy route tests)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fake multi-account manager
# ---------------------------------------------------------------------------

class FakeWechatManager:
    def __init__(self, accounts: list[dict[str, Any]] | None = None) -> None:
        self._accounts: list[dict[str, Any]] = accounts if accounts is not None else [
            {"id": "default", "name": "默认账号"}
        ]
        self._statuses: dict[str, FakeWechatService] = {}
        self.added: list[dict[str, Any]] = []
        self.removed: list[str] = []

    def _get(self, account_id: str) -> FakeWechatService:
        if account_id not in self._statuses:
            self._statuses[account_id] = FakeWechatService()
        return self._statuses[account_id]

    def _status_dict(self, status: WechatChannelStatus) -> dict[str, Any]:
        return {
            "running": status.running,
            "login_state": status.login_state,
            "qrcode_url": status.qrcode_url,
            "qrcode_data_url": status.qrcode_data_url,
            "qrcode_status": status.qrcode_status,
            "user": status.user,
            "error": status.error,
        }

    async def all_statuses(self) -> list[dict[str, Any]]:
        result = []
        for a in self._accounts:
            svc = self._get(a["id"])
            s = await svc.status()
            result.append({"id": a["id"], "name": a.get("name", a["id"]), "status": self._status_dict(s)})
        return result

    async def account_status(self, account_id: str) -> dict[str, Any]:
        svc = self._get(account_id)
        s = await svc.status()
        return {"id": account_id, "name": account_id, "status": self._status_dict(s)}

    async def start_account(self, account_id: str) -> dict[str, Any]:
        svc = self._get(account_id)
        s = await svc.start()
        return {"id": account_id, "name": account_id, "status": self._status_dict(s)}

    async def stop_account(self, account_id: str) -> dict[str, Any]:
        svc = self._get(account_id)
        s = await svc.stop()
        return {"id": account_id, "name": account_id, "status": self._status_dict(s)}

    async def add_account(self, config: dict[str, Any]) -> dict[str, Any]:
        self.added.append(config)
        return {"id": config["id"], "name": config.get("name", config["id"])}

    async def remove_account(self, account_id: str) -> None:
        self.removed.append(account_id)

    async def auto_start_all(self) -> None:
        pass

    async def stop_all(self) -> None:
        pass

    def first_account_id(self) -> str | None:
        if self._accounts:
            return self._accounts[0]["id"]
        return None


class FakeAgentChatService:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def chat(self, agent_id: str, content: str) -> str:
        self.calls.append({"agent_id": agent_id, "content": content})
        return f"reply-{len(self.calls)}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_client(manager: FakeWechatManager | None = None) -> TestClient:
    from server.adapter.auth_routes import create_auth_router

    mgr = manager or FakeWechatManager()
    token = AuthToken("test-token")
    container = AppContainer(
        auth_service=AuthService(token),
        config_file_service=MagicMock(),
        proxy_service=MagicMock(),
        system_log_service=MagicMock(),
        system_update_service=MagicMock(),
        session_codec=SessionCodec("test-token"),
        wechat_channel_manager=mgr,
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


def write_wechat_config(workspace: Path) -> None:
    workspace.joinpath("config.yaml").write_text(
        """
agents:
  default_agent_id: assistant
channels:
  wechat_personal:
    enabled: true
    default_agent_id: assistant
    accounts:
      - id: default
        name: 默认账号
        default_agent_id: assistant
      - id: wife
        name: wife
        default_agent_id: assistant
""",
        encoding="utf-8",
    )


def text_msg(text: str) -> dict[str, Any]:
    return {
        "message_type": 1,
        "from_user_id": "wechat-user",
        "to_user_id": "bot-user",
        "context_token": f"context-{text}",
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }


# ---------------------------------------------------------------------------
# Legacy route tests
# ---------------------------------------------------------------------------

class TestWechatChannelRoutes:
    def test_requires_auth(self) -> None:
        client = make_client()
        resp = client.get("/api/channels/wechat/status")
        assert resp.status_code == 401

    def test_start_and_stop(self) -> None:
        mgr = FakeWechatManager()
        client = make_client(mgr)
        headers = auth_headers(client)
        resp = client.post("/api/channels/wechat/start", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["wechat"]["login_state"] == "starting"
        resp = client.post("/api/channels/wechat/stop", headers=headers)
        assert resp.status_code == 200

    def test_status_returns_fields(self) -> None:
        mgr = FakeWechatManager()
        client = make_client(mgr)
        headers = auth_headers(client)
        resp = client.get("/api/channels/wechat/status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["wechat"]
        for field in (
            "running", "login_state", "qrcode_url", "qrcode_data_url",
            "qrcode_status", "user", "error",
        ):
            assert field in data


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------

class TestWechatChannelService:
    def test_initial_status_stopped(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        assert svc._login_state == "stopped"
        assert svc._user == ""

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

    def test_account_id_default(self) -> None:
        svc = WechatChannelService(Path("/tmp"))
        assert svc._account_id == "default"

    def test_account_id_custom(self) -> None:
        svc = WechatChannelService(Path("/tmp"), account_id="work")
        assert svc._account_id == "work"

    def test_session_path_includes_account_id(self) -> None:
        svc = WechatChannelService(Path("/tmp"), account_id="work")
        assert "work" in str(svc._session_path)

    @pytest.mark.asyncio
    async def test_wechat_account_reuses_session_within_24_hours(self, tmp_path: Path) -> None:
        write_wechat_config(tmp_path)
        agent = FakeAgentChatService()
        chat_sessions = ChatSessionService(tmp_path)
        svc = WechatChannelService(
            tmp_path,
            agent_chat_service=agent,
            chat_session_service=chat_sessions,
            account_id="wife",
        )

        await svc._process_message(text_msg("first"))
        await svc._process_message(text_msg("second"))

        summaries = chat_sessions.list_sessions()
        assert len(summaries) == 1
        session = chat_sessions.get_session(summaries[0].id)
        assert session.title == "微信 wife"
        assert [msg.content for msg in session.messages] == [
            "first",
            "reply-1",
            "second",
            "reply-2",
        ]
        assert agent.calls[1]["content"].startswith("以下是同一微信账号在 24 小时 session 内的对话上下文")
        assert "用户: first" in agent.calls[1]["content"]

    @pytest.mark.asyncio
    async def test_wechat_account_opens_new_session_after_24_hours(self, tmp_path: Path) -> None:
        write_wechat_config(tmp_path)
        agent = FakeAgentChatService()
        chat_sessions = ChatSessionService(tmp_path)
        svc = WechatChannelService(
            tmp_path,
            agent_chat_service=agent,
            chat_session_service=chat_sessions,
            account_id="default",
        )

        await svc._process_message(text_msg("old"))
        index_path = tmp_path / "channels" / "wechat_sessions" / "default.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["last_message_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        index_path.write_text(json.dumps(index), encoding="utf-8")

        await svc._process_message(text_msg("new"))

        summaries = chat_sessions.list_sessions()
        assert len(summaries) == 2
        newest = chat_sessions.get_session(summaries[0].id)
        assert [msg.content for msg in newest.messages] == ["new", "reply-2"]
        assert agent.calls[1]["content"] == "new"

    @pytest.mark.asyncio
    async def test_wechat_context_compresses_long_session_history(self, tmp_path: Path) -> None:
        write_wechat_config(tmp_path)
        agent = FakeAgentChatService()
        chat_sessions = ChatSessionService(tmp_path)
        session = chat_sessions.create_session("assistant", "微信 default")
        for index in range(10):
            chat_sessions.append_message(
                session.id,
                ChatMessageData(role="user", content=f"early-user-{index} " + ("x" * 120)),
            )
            chat_sessions.append_message(
                session.id,
                ChatMessageData(role="assistant", content=f"early-assistant-{index} " + ("y" * 120)),
            )
        for index in range(4):
            chat_sessions.append_message(
                session.id,
                ChatMessageData(role="user", content=f"recent-user-{index}"),
            )
            chat_sessions.append_message(
                session.id,
                ChatMessageData(role="assistant", content=f"recent-assistant-{index}"),
            )
        index_path = tmp_path / "channels" / "wechat_sessions" / "default.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(
                {
                    "account_id": "default",
                    "agent_id": "assistant",
                    "session_id": session.id,
                    "last_message_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        svc = WechatChannelService(
            tmp_path,
            agent_chat_service=agent,
            chat_session_service=chat_sessions,
            account_id="default",
        )

        await svc._process_message(text_msg("current"))

        content = agent.calls[0]["content"]
        assert "较早消息摘要" in content
        assert "最近消息" in content
        assert "early-user-0" in content
        assert "recent-user-3" in content
        assert content.endswith("current")


# ---------------------------------------------------------------------------
# iLink / QR tests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Multi-account route tests
# ---------------------------------------------------------------------------

class TestWechatChannelMultiAccountRoutes:
    def test_list_accounts(self) -> None:
        mgr = FakeWechatManager([
            {"id": "main", "name": "主账号"},
            {"id": "work", "name": "工作微信"},
        ])
        client = make_client(mgr)
        resp = client.get("/api/channels/wechat/accounts", headers=auth_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["accounts"]) == 2
        assert data["accounts"][0]["id"] == "main"
        assert data["accounts"][1]["id"] == "work"

    def test_create_account(self) -> None:
        mgr = FakeWechatManager()
        client = make_client(mgr)
        resp = client.post(
            "/api/channels/wechat/accounts",
            json={"id": "newbot", "name": "新账号", "default_agent_id": "assistant"},
            headers=auth_headers(client),
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert mgr.added[0]["id"] == "newbot"

    def test_create_account_missing_id(self) -> None:
        mgr = FakeWechatManager()
        client = make_client(mgr)
        resp = client.post(
            "/api/channels/wechat/accounts",
            json={"name": "no id"},
            headers=auth_headers(client),
        )
        assert resp.status_code == 422

    def test_delete_account(self) -> None:
        mgr = FakeWechatManager()
        client = make_client(mgr)
        resp = client.delete(
            "/api/channels/wechat/accounts/default",
            headers=auth_headers(client),
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_get_account_status(self) -> None:
        mgr = FakeWechatManager()
        client = make_client(mgr)
        resp = client.get(
            "/api/channels/wechat/accounts/default/status",
            headers=auth_headers(client),
        )
        assert resp.status_code == 200
        assert resp.json()["account"]["id"] == "default"

    def test_start_account(self) -> None:
        mgr = FakeWechatManager()
        client = make_client(mgr)
        resp = client.post(
            "/api/channels/wechat/accounts/default/start",
            headers=auth_headers(client),
        )
        assert resp.status_code == 200
        assert resp.json()["account"]["status"]["login_state"] == "starting"

    def test_stop_account(self) -> None:
        mgr = FakeWechatManager()
        client = make_client(mgr)
        resp = client.post(
            "/api/channels/wechat/accounts/default/stop",
            headers=auth_headers(client),
        )
        assert resp.status_code == 200

    def test_legacy_status_with_no_accounts(self) -> None:
        mgr = FakeWechatManager([])
        client = make_client(mgr)
        resp = client.get("/api/channels/wechat/status", headers=auth_headers(client))
        assert resp.status_code == 200
        assert resp.json()["wechat"] is None

    def test_legacy_start_with_no_accounts(self) -> None:
        mgr = FakeWechatManager([])
        client = make_client(mgr)
        resp = client.post("/api/channels/wechat/start", headers=auth_headers(client))
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# WechatChannelManager tests (with real manager, tmp config)
# ---------------------------------------------------------------------------

class TestWechatChannelManager:
    def test_parse_accounts_legacy_enabled(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("""
channels:
  wechat_personal:
    enabled: true
    default_agent_id: "assistant"
    auto_start: false
    proxy: ""
""", encoding="utf-8")
        mgr = WechatChannelManager(tmp_path)
        accounts = mgr.parse_accounts()
        assert len(accounts) == 1
        assert accounts[0]["id"] == "default"
        assert accounts[0]["name"] == "默认账号"

    def test_parse_accounts_legacy_disabled(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("""
channels:
  wechat_personal:
    enabled: false
""", encoding="utf-8")
        mgr = WechatChannelManager(tmp_path)
        accounts = mgr.parse_accounts()
        assert accounts == []

    def test_parse_accounts_multi(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("""
channels:
  wechat_personal:
    enabled: true
    default_agent_id: "assistant"
    accounts:
      - id: "main"
        name: "主账号"
        auto_start: true
      - id: "work"
        name: "工作微信"
""", encoding="utf-8")
        mgr = WechatChannelManager(tmp_path)
        accounts = mgr.parse_accounts()
        assert len(accounts) == 2
        assert accounts[0]["id"] == "main"
        assert accounts[0]["auto_start"] is True
        assert accounts[1]["id"] == "work"

    @pytest.mark.asyncio
    async def test_add_account_writes_config(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("""
channels:
  wechat_personal:
    enabled: true
    accounts:
      - id: "main"
        name: "主账号"
""", encoding="utf-8")
        mgr = WechatChannelManager(tmp_path)
        await mgr.add_account({"id": "work", "name": "工作微信"})
        accounts = mgr.parse_accounts()
        assert len(accounts) == 2
        raw = mgr._read_raw_config()
        saved = raw["channels"]["wechat_personal"]["accounts"]
        assert len(saved) == 2

    @pytest.mark.asyncio
    async def test_add_account_duplicate_rejected(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("""
channels:
  wechat_personal:
    accounts:
      - id: "main"
        name: "主账号"
""", encoding="utf-8")
        mgr = WechatChannelManager(tmp_path)
        from server.app.wechat_channel_manager import WechatChannelManagerError
        with pytest.raises(WechatChannelManagerError, match="已存在"):
            await mgr.add_account({"id": "main", "name": "dup"})

    @pytest.mark.asyncio
    async def test_remove_account(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("""
channels:
  wechat_personal:
    accounts:
      - id: "main"
        name: "主账号"
      - id: "work"
        name: "工作微信"
""", encoding="utf-8")
        mgr = WechatChannelManager(tmp_path)
        await mgr.remove_account("work")
        accounts = mgr.parse_accounts()
        assert len(accounts) == 1
        assert accounts[0]["id"] == "main"

    def test_first_account_id(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("""
channels:
  wechat_personal:
    accounts:
      - id: "main"
        name: "主账号"
""", encoding="utf-8")
        mgr = WechatChannelManager(tmp_path)
        assert mgr.first_account_id() == "main"

    def test_first_account_id_empty(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("""
channels:
  wechat_personal:
    enabled: false
""", encoding="utf-8")
        mgr = WechatChannelManager(tmp_path)
        assert mgr.first_account_id() is None
