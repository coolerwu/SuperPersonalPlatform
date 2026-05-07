from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from server.adapter.auth_routes import SESSION_COOKIE, current_session_codec
from server.adapter.dependencies import AppContainer


class InvalidTerminalMessageError(Exception):
    pass


class TerminalAuthenticationError(Exception):
    pass


def create_terminal_router(container: AppContainer) -> APIRouter:
    router = APIRouter(
        prefix="/api/system/terminal",
        tags=["terminal"],
    )

    @router.websocket("/connect")
    async def connect_terminal(websocket: WebSocket) -> None:
        session_cookie = websocket.cookies.get(SESSION_COOKIE)
        if not _verify_current_session(container, session_cookie):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()

        async def receive_terminal_message() -> dict[str, object]:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                raise InvalidTerminalMessageError("terminal message must be an object")
            if not _verify_current_session(container, session_cookie):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                raise TerminalAuthenticationError("terminal session is no longer authenticated")
            return data

        async def send_terminal_output(text: str) -> None:
            await websocket.send_json({"type": "output", "data": text})

        try:
            await container.terminal_session_service.run_interactive_session(
                receive_terminal_message,
                send_terminal_output,
            )
        except WebSocketDisconnect:
            return
        except TerminalAuthenticationError:
            return
        except InvalidTerminalMessageError:
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)

    return router


def _verify_current_session(container: AppContainer, session_cookie: str | None) -> bool:
    return current_session_codec(container).verify(session_cookie)
