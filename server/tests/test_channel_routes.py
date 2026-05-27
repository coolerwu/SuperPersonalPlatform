from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.auth_routes import create_auth_router
from server.adapter.channel_routes import create_channel_router
from server.adapter.dependencies import AppContainer
from server.app.auth_service import AuthService
from server.app.wechat_channel_service import WechatChannelService
from server.domain.auth import AuthToken
from server.infrastructure.session import SessionCodec


class FakeWechatService:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def status(self):
        return {
            "running": False,
            "login_state": "stopped",
            "qrcode_url": "",
            "qrcode_data_url": "",
            "qrcode_status": "",
            "user": "",
            "error": "",
            "logs": (),
        }

    def start(self):
        self.started = True
        return {**self.status(), "running": True, "login_state": "starting"}

    def stop(self):
        self.stopped = True
        return self.status()


def make_client(tmp_path: Path, service: FakeWechatService | None = None) -> TestClient:
    container = AppContainer(
        auth_service=AuthService(AuthToken("secret-token")),
        config_file_service=None,
        proxy_service=None,
        system_log_service=None,
        system_update_service=None,
        session_codec=SessionCodec("secret-token"),
        wechat_channel_service=service or FakeWechatService(),
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_channel_router(container))
    return TestClient(app)


def test_wechat_channel_requires_authentication(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/channels/wechat/status")

    assert response.status_code == 401


def test_wechat_channel_start_and_stop(tmp_path) -> None:
    service = FakeWechatService()
    client = make_client(tmp_path, service)
    client.post("/api/auth/login", json={"token": "secret-token"})

    start_response = client.post("/api/channels/wechat/start")
    stop_response = client.post("/api/channels/wechat/stop")

    assert start_response.status_code == 200
    assert start_response.json()["wechat"]["login_state"] == "starting"
    assert stop_response.status_code == 200
    assert service.started
    assert service.stopped


def test_wechat_sidecar_env_drops_inherited_proxy(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("channels:\n  wechat_personal: {}\n", encoding="utf-8")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    service = WechatChannelService(workspace, tmp_path)

    env = service._sidecar_env()

    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env


def test_wechat_sidecar_env_uses_explicit_proxy(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        "channels:\n  wechat_personal:\n    proxy: http://10.0.0.2:7890\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    service = WechatChannelService(workspace, tmp_path)

    env = service._sidecar_env()

    assert env["HTTP_PROXY"] == "http://10.0.0.2:7890"
    assert env["HTTPS_PROXY"] == "http://10.0.0.2:7890"
