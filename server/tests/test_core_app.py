from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.auth_routes import create_auth_router
from server.adapter.channel_routes import create_channel_router
from server.adapter.dependencies import AppContainer
from server.adapter.system_routes import create_system_router
from server.adapter.workspace_routes import create_workspace_router
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.nutstore_service import NutstoreService
from server.app.run_service import RunService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import UpdateAlreadyRunningError
from server.app.wechat_channel_service import WechatChannelStatus
from server.app.workspace_file_service import WorkspaceFileService
from server.domain.auth import AuthToken
from server.infrastructure.config import AuthConfig, NutstoreConfig, ServerConfig, Settings
from server.infrastructure.config import parse_settings
from server.infrastructure.fastapi_app import create_app
from server.infrastructure.session import SessionCodec


CONFIG = """\
auth:
  token: secret-token
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://api.openai.com/v1
      api_key: test-key
      model: gpt-4o-mini
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.
      model_id: default
"""


class FakeUpdateService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.called = False

    def start_update(self) -> Path:
        self.called = True
        if self.error:
            raise self.error
        return Path("logs/platform-2026-08-20.log")


class FakeWechatManager:
    async def all_statuses(self):
        return [{"id": "main", "name": "主账号", "status": _wechat_status()}]

    async def start_account(self, account_id: str):
        return {"id": account_id, "name": account_id, "status": _wechat_status("connecting")}

    async def stop_account(self, account_id: str):
        return {"id": account_id, "name": account_id, "status": _wechat_status("stopped")}

    async def account_status(self, account_id: str):
        return {"id": account_id, "name": account_id, "status": _wechat_status()}

    async def add_account(self, config):
        return config

    async def update_account(self, account_id, config):
        return {"id": account_id, **config}

    async def remove_account(self, account_id):
        return None

    def first_account_id(self):
        return "main"


def make_app_client(tmp_path: Path) -> TestClient:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    settings = Settings(
        auth=AuthConfig(token="secret-token"),
        server=ServerConfig(),
        nutstore=NutstoreConfig(),
    )
    return TestClient(create_app(settings, workspace=tmp_path))


def make_system_client(tmp_path: Path, update_service: FakeUpdateService | None = None) -> TestClient:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    token = "secret-token"
    container = AppContainer(
        auth_service=AuthService(AuthToken(token)),
        config_file_service=ConfigFileService(tmp_path),
        run_service=RunService(tmp_path),
        nutstore_service=NutstoreService(NutstoreConfig()),
        system_log_service=SystemLogService(tmp_path),
        system_update_service=update_service or FakeUpdateService(),
        workspace_file_service=WorkspaceFileService(tmp_path),
        session_codec=SessionCodec(token),
        wechat_channel_manager=FakeWechatManager(),
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_channel_router(container))
    app.include_router(create_system_router(container))
    app.include_router(create_workspace_router(container))
    return TestClient(app)


def test_login_uses_current_workspace_token_without_restart(tmp_path) -> None:
    client = make_app_client(tmp_path)

    assert client.post("/api/auth/login", json={"token": "secret-token"}).status_code == 200
    assert client.get("/api/auth/me").json() == {"authenticated": True}

    (tmp_path / "config.yaml").write_text(CONFIG.replace("secret-token", "next-token"), encoding="utf-8")

    assert client.get("/api/auth/me").json() == {"authenticated": False}
    assert client.post("/api/auth/login", json={"token": "next-token"}).status_code == 200


def test_old_product_routes_are_gone(tmp_path) -> None:
    client = make_app_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    assert client.get("/api/agents/options").status_code == 404
    assert client.get("/api/sessions").status_code == 404
    assert client.get("/api/portfolio/holdings").status_code == 404
    assert client.get("/api/proxy/site/").status_code == 404


def test_legacy_workspace_config_fields_are_ignored() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "proxy": {"upstream_base_url": "http://example.test/"},
            "portfolio": {"agent_id": "assistant"},
            "skills": {"definitions": [{"id": "common:old"}]},
            "llm": {
                "default_model_id": "default",
                "models": [
                    {
                        "id": "default",
                        "name": "Default",
                        "provider": "openai_compatible",
                        "base_url": "https://api.openai.com/v1",
                        "api_key": "test-key",
                        "model": "gpt-4o-mini",
                        "mode": "agent",
                        "runtime": "deepagent",
                    }
                ],
            },
            "agents": {
                "builtin_overrides": {},
                "definitions": [
                    {
                        "id": "assistant",
                        "name": "Assistant",
                        "system_prompt": "Be direct.",
                        "model_id": "default",
                        "skill_ids": ["common:old"],
                    }
                ],
            },
        }
    )

    assert settings.agent_workspace.get_agent("assistant").context_ids == ()


def test_system_update_routes_do_not_expose_config_editor(tmp_path) -> None:
    update_service = FakeUpdateService()
    client = make_system_client(tmp_path, update_service)
    client.post("/api/auth/login", json={"token": "secret-token"})

    assert client.post("/api/system/config/read").status_code == 404
    assert client.put("/api/system/config", json={"content": CONFIG}).status_code == 404

    update_response = client.post("/api/system/update-service")
    assert update_response.status_code == 200
    assert update_service.called is True


def test_system_update_conflict(tmp_path) -> None:
    client = make_system_client(tmp_path, FakeUpdateService(UpdateAlreadyRunningError()))
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.post("/api/system/update-service")

    assert response.status_code == 409


def test_workspace_file_routes_are_scoped_and_edit_text(tmp_path) -> None:
    client = make_system_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "index.json").write_text('{"runs":[]}', encoding="utf-8")

    list_response = client.post("/api/workspace/list", json={"path": "runs"})
    assert list_response.status_code == 200
    assert list_response.json()["entries"][0]["path"] == "runs/index.json"
    assert list_response.json()["entries"][0]["deletable"] is True

    read_response = client.post("/api/workspace/read", json={"path": "runs/index.json"})
    assert read_response.status_code == 200
    assert read_response.json()["content"] == '{"runs":[]}'

    save_response = client.put(
        "/api/workspace/write",
        json={"path": "runs/index.json", "content": '{"runs":[{"status":"completed"}]}'},
    )
    assert save_response.status_code == 200
    assert (runs_dir / "index.json").read_text(encoding="utf-8") == '{"runs":[{"status":"completed"}]}'

    escape_response = client.post("/api/workspace/list", json={"path": "../"})
    assert escape_response.status_code == 400


def test_workspace_config_write_validates_settings(tmp_path) -> None:
    client = make_system_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    updated = CONFIG.replace("Be direct.", "Be concise.")
    save_response = client.put(
        "/api/workspace/write",
        json={"path": "config.yaml", "content": updated},
    )
    assert save_response.status_code == 200
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == updated

    invalid_response = client.put(
        "/api/workspace/write",
        json={"path": "config.yaml", "content": "auth: []"},
    )
    assert invalid_response.status_code == 400


def test_workspace_delete_protects_config_and_root_skeleton(tmp_path) -> None:
    client = make_system_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})
    (tmp_path / "runs").mkdir()
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    (scratch_dir / "note.txt").write_text("delete me", encoding="utf-8")

    root_response = client.post("/api/workspace/list", json={"path": ""})
    root_entries = {entry["path"]: entry for entry in root_response.json()["entries"]}
    assert root_entries["config.yaml"]["deletable"] is False
    assert root_entries["runs"]["deletable"] is False
    assert root_entries["scratch"]["deletable"] is True

    assert client.post("/api/workspace/delete", json={"path": "config.yaml"}).status_code == 400
    assert client.post("/api/workspace/delete", json={"path": "runs"}).status_code == 400

    delete_response = client.post("/api/workspace/delete", json={"path": "scratch"})
    assert delete_response.status_code == 200
    assert not scratch_dir.exists()


def test_wechat_account_routes(tmp_path) -> None:
    client = make_system_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    assert client.get("/api/channels/wechat/accounts").json()["accounts"][0]["id"] == "main"
    assert client.post("/api/channels/wechat/accounts/main/start").json()["account"]["status"]["login_state"] == "connecting"
    assert client.post("/api/channels/wechat/accounts/main/stop").json()["account"]["status"]["login_state"] == "stopped"


def _wechat_status(login_state: str = "logged_in") -> dict[str, object]:
    status = WechatChannelStatus(
        running=login_state != "stopped",
        login_state=login_state,
        qrcode_url="",
        qrcode_data_url="",
        qrcode_status="",
        user="",
        error="",
        logs=(),
    )
    return {
        "running": status.running,
        "login_state": status.login_state,
        "qrcode_url": status.qrcode_url,
        "qrcode_data_url": status.qrcode_data_url,
        "qrcode_status": status.qrcode_status,
        "user": status.user,
        "error": status.error,
    }
