import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.adapter.terminal_routes import create_terminal_router
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.proxy_service import ProxyService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import SystemUpdateService
from server.app.terminal_session_service import TerminalSessionService
from server.domain.auth import AuthToken
from server.domain.proxy import ProxyRequest, ProxyResponse
from server.infrastructure.session import SessionCodec


class EmptyProxyGateway:
    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        return ProxyResponse(status_code=200, headers={}, body=b"")


class ReceiveOnceTerminalService:
    async def run_interactive_session(self, receive_message, send_text) -> None:
        await receive_message()


def write_config(workspace: Path, token: str = "secret-token") -> None:
    (workspace / "config.yaml").write_text(
        f"auth:\n  token: {token}\nproxy:\n  upstream_base_url: http://example.test/\n",
        encoding="utf-8",
    )


def make_client(workspace: Path, terminal_service=None) -> TestClient:
    token = "secret-token"
    container = AppContainer(
        auth_service=AuthService(AuthToken(token)),
        config_file_service=ConfigFileService(workspace),
        proxy_service=ProxyService(EmptyProxyGateway()),
        system_log_service=SystemLogService(workspace),
        system_update_service=SystemUpdateService(workspace, workspace),
        terminal_session_service=terminal_service or TerminalSessionService(workspace, workspace),
        session_codec=SessionCodec(token),
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_terminal_router(container))
    return TestClient(app)


def test_terminal_history_routes_are_not_exposed(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post("/api/system/terminal/sessions/list")

    assert response.status_code == 404


def test_terminal_websocket_requires_authentication(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)

    try:
        with client.websocket_connect("/api/system/terminal/connect"):
            raise AssertionError("expected websocket authentication failure")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_terminal_websocket_rechecks_current_token_for_each_message(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path, ReceiveOnceTerminalService())
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/system/terminal/connect") as websocket:
        write_config(tmp_path, token="changed-token")
        websocket.send_json({"type": "input", "data": "ls\r"})
        try:
            websocket.receive_json()
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
        else:
            raise AssertionError("expected websocket to close after token changed")


def test_terminal_service_runs_without_persisting_transcript(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/sh")
    service = TerminalSessionService(tmp_path, tmp_path)
    messages = [
        {"type": "resize", "cols": 120, "rows": 34},
        {"type": "input", "data": "printf codex-terminal-test\rexit\r"},
    ]
    outputs = []
    resizes = []

    async def receive_message() -> dict[str, object]:
        if messages:
            return messages.pop(0)
        await asyncio.sleep(60)
        return {"type": "input", "data": ""}

    async def send_text(text: str) -> None:
        outputs.append(text)

    def fake_resize(fd: int, cols: int, rows: int) -> None:
        resizes.append((fd, cols, rows))

    monkeypatch.setattr(service, "resize_pty", fake_resize)

    asyncio.run(
        asyncio.wait_for(
            service.run_interactive_session(receive_message, send_text),
            timeout=5,
        )
    )

    assert not (tmp_path / "terminal" / "sessions").exists()
    assert any("codex-terminal-test" in output for output in outputs)
    assert resizes[0][1:] == (120, 34)
