import asyncio
import json
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


def make_client(workspace: Path) -> TestClient:
    token = "secret-token"
    container = AppContainer(
        auth_service=AuthService(AuthToken(token)),
        config_file_service=ConfigFileService(workspace),
        proxy_service=ProxyService(EmptyProxyGateway()),
        system_log_service=SystemLogService(workspace),
        system_update_service=SystemUpdateService(workspace, workspace),
        terminal_session_service=TerminalSessionService(workspace, workspace),
        session_codec=SessionCodec(token),
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_terminal_router(container))
    return TestClient(app)


def test_terminal_sessions_require_authentication(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post("/api/system/terminal/sessions/list")

    assert response.status_code == 401


def test_terminal_websocket_requires_authentication(tmp_path) -> None:
    client = make_client(tmp_path)

    try:
        with client.websocket_connect("/api/system/terminal/connect"):
            raise AssertionError("expected websocket authentication failure")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_terminal_sessions_list_and_read_workspace_transcripts(tmp_path) -> None:
    sessions_dir = tmp_path / "terminal" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_path = sessions_dir / "terminal-2026-05-06T143012-abcdef12.jsonl"
    session_path.write_text(
        '{"timestamp":"2026-05-06T14:30:12","stream":"input","content":"pwd\\n"}\n',
        encoding="utf-8",
    )
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    list_response = client.post("/api/system/terminal/sessions/list")
    read_response = client.post(
        "/api/system/terminal/sessions/read",
        json={"name": session_path.name},
    )
    unsafe_response = client.post(
        "/api/system/terminal/sessions/read",
        json={"name": "../config.yaml"},
    )

    assert list_response.status_code == 200
    assert list_response.json()["sessions"][0]["name"] == session_path.name
    assert read_response.status_code == 200
    assert "pwd" in read_response.json()["content"]
    assert unsafe_response.status_code == 400


def test_terminal_service_writes_interactive_transcript(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/sh")
    service = TerminalSessionService(tmp_path, tmp_path)
    sent = False
    outputs = []

    async def receive_text() -> str:
        nonlocal sent
        if not sent:
            sent = True
            return "printf codex-terminal-test\rexit\r"
        await asyncio.sleep(60)
        return ""

    async def send_text(text: str) -> None:
        outputs.append(text)

    asyncio.run(
        asyncio.wait_for(
            service.run_interactive_session(receive_text, send_text),
            timeout=5,
        )
    )

    session_files = list((tmp_path / "terminal" / "sessions").glob("terminal-*.jsonl"))
    assert len(session_files) == 1
    transcript = [
        json.loads(line)
        for line in session_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        event["stream"] == "input" and "codex-terminal-test" in event["content"]
        for event in transcript
    )
    assert any(
        event["stream"] == "output" and "codex-terminal-test" in event["content"]
        for event in transcript
    )
    assert any("codex-terminal-test" in output for output in outputs)
