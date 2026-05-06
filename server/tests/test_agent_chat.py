from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.agent_routes import create_agent_router
from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.app.agent_chat_service import AgentChatService, ChatImage
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.domain.agents import ModelDefinition
from server.domain.auth import AuthToken
from server.infrastructure.config import load_settings, parse_settings
from server.infrastructure.session import SessionCodec


class FakeModelGateway:
    def __init__(self) -> None:
        self.calls = []

    async def complete(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...] = (),
    ) -> str:
        self.calls.append(
            {
                "model": model.id,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "images": images,
            }
        )
        return f"{model.model}: {system_prompt[:7]} / {user_message} / images={len(images)}"


def write_config(
    workspace: Path,
    *,
    default_model_id: str = "fast",
    default_agent_id: str = "assistant",
    agent_model_id: str = "fast",
    supports_images: bool = True,
) -> None:
    workspace.joinpath("config.yaml").write_text(
        f"""
auth:
  token: secret-token
proxy:
  upstream_base_url: http://example.test/
llm:
  default_model_id: {default_model_id}
  models:
    - id: fast
      name: Fast Model
      base_url: https://llm.example.test/v1
      api_key: top-secret-key
      model: fast-chat
      temperature: 0.2
      supports_images: {str(supports_images).lower()}
agents:
  default_agent_id: {default_agent_id}
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: You are concise.
      model_id: {agent_model_id}
""".strip(),
        encoding="utf-8",
    )


def make_client(workspace: Path, gateway: FakeModelGateway | None = None) -> TestClient:
    config_service = ConfigFileService(workspace)
    container = AppContainer(
        auth_service=AuthService(AuthToken("secret-token")),
        config_file_service=config_service,
        proxy_service=None,
        system_log_service=None,
        system_update_service=None,
        terminal_session_service=None,
        session_codec=SessionCodec("secret-token"),
        agent_chat_service=AgentChatService(config_service.config_path, gateway or FakeModelGateway()),
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_agent_router(container))
    return TestClient(app)


def test_agent_config_defaults_without_permission_gate() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "proxy": {"upstream_base_url": "http://example.test/"},
        }
    )

    assert settings.agent_platform.models == ()
    assert settings.agent_platform.agents == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"default_model_id": "missing"}, "llm.default_model_id"),
        ({"default_agent_id": "missing"}, "agents.default_agent_id"),
        ({"agent_model_id": "missing"}, "model_id"),
    ],
)
def test_agent_config_rejects_invalid_references(tmp_path, overrides, message) -> None:
    write_config(tmp_path, **overrides)

    with pytest.raises(ValueError, match=message):
        load_settings(tmp_path / "config.yaml")


def test_agent_options_require_auth_and_do_not_leak_api_key(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)

    assert client.get("/api/agents/options").status_code == 401
    assert client.post("/api/auth/login", json={"token": "secret-token"}).status_code == 200

    response = client.get("/api/agents/options")

    assert response.status_code == 200
    body = response.json()
    assert body["default_agent_id"] == "assistant"
    assert "top-secret-key" not in str(body)
    assert body["agents"] == [
        {
            "id": "assistant",
            "name": "Assistant",
            "model_id": "fast",
            "model": {
                "id": "fast",
                "name": "Fast Model",
                "model": "fast-chat",
                "base_url": "https://llm.example.test/v1",
                "supports_images": True,
                "has_api_key": True,
            },
        }
    ]


def test_agent_config_endpoint_masks_api_keys(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.get("/api/agents/config")

    assert response.status_code == 200
    body = response.json()
    assert body["path"].endswith("config.yaml")
    assert "top-secret-key" not in str(body)
    assert body["models"][0]["has_api_key"] is True
    assert body["models"][0]["api_key_mask"] == "********"


def test_agent_config_update_preserves_existing_api_key(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.put(
        "/api/agents/config",
        json={
            "default_model_id": "fast",
            "default_agent_id": "assistant",
            "models": [
                {
                    "id": "fast",
                    "name": "Renamed Model",
                    "base_url": "https://llm.example.test/v1",
                    "model": "fast-chat",
                    "api_key": "",
                    "temperature": 0.4,
                    "supports_images": False,
                }
            ],
            "agents": [
                {
                    "id": "assistant",
                    "name": "Assistant",
                    "model_id": "fast",
                    "system_prompt": "You are direct.",
                }
            ],
        },
    )

    assert response.status_code == 200
    settings = load_settings(tmp_path / "config.yaml")
    assert settings.agent_platform.get_model("fast").api_key == "top-secret-key"
    assert settings.agent_platform.get_model("fast").name == "Renamed Model"
    assert settings.agent_platform.get_model("fast").supports_images is False


def test_agent_chat_websocket_uses_agent_bound_model_without_model_id(tmp_path) -> None:
    write_config(tmp_path)
    gateway = FakeModelGateway()
    client = make_client(tmp_path, gateway)
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/agents/chat/connect") as websocket:
        assert websocket.receive_json() == {"type": "status", "status": "connected"}
        websocket.send_json(
            {
                "type": "message",
                "agent_id": "assistant",
                "content": "你好",
            }
        )
        assert websocket.receive_json() == {"type": "status", "status": "running"}
        assert websocket.receive_json() == {
            "type": "assistant_message",
            "content": "fast-chat: You are / 你好 / images=0",
        }
        assert websocket.receive_json() == {"type": "status", "status": "idle"}

    assert gateway.calls[0]["model"] == "fast"


def test_agent_chat_websocket_passes_images_to_adapter(tmp_path) -> None:
    write_config(tmp_path, supports_images=True)
    gateway = FakeModelGateway()
    client = make_client(tmp_path, gateway)
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/agents/chat/connect") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "message",
                "agent_id": "assistant",
                "content": "看图",
                "images": [{"mime_type": "image/png", "data": "aW1hZ2U="}],
            }
        )
        websocket.receive_json()
        assert websocket.receive_json()["content"].endswith("images=1")

    assert gateway.calls[0]["images"] == (ChatImage(mime_type="image/png", data="aW1hZ2U="),)


def test_agent_chat_websocket_rejects_images_for_text_model(tmp_path) -> None:
    write_config(tmp_path, supports_images=False)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/agents/chat/connect") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "message",
                "agent_id": "assistant",
                "content": "看图",
                "images": [{"mime_type": "image/png", "data": "aW1hZ2U="}],
            }
        )
        assert websocket.receive_json() == {"type": "status", "status": "running"}
        assert websocket.receive_json() == {
            "type": "error",
            "message": "当前模型不支持图片输入",
        }
