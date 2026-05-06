from pydantic import BaseModel
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from server.adapter.auth_routes import SESSION_COOKIE
from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.terminal_session_service import InvalidTerminalSessionError


class TerminalSessionReadRequest(BaseModel):
    name: str


class InvalidTerminalMessageError(Exception):
    pass


def create_terminal_router(container: AppContainer) -> APIRouter:
    def require_terminal_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/system/terminal",
        tags=["terminal"],
    )

    @router.post("/sessions/list", dependencies=[Depends(require_terminal_auth)])
    def list_sessions() -> dict[str, list[dict[str, str | int]]]:
        return {
            "sessions": [
                {
                    "name": session.name,
                    "path": session.path,
                    "size": session.size,
                    "modified_at": session.modified_at,
                }
                for session in container.terminal_session_service.list_sessions()
            ]
        }

    @router.post("/sessions/read", dependencies=[Depends(require_terminal_auth)])
    def read_session(payload: TerminalSessionReadRequest) -> dict[str, str | int]:
        try:
            session = container.terminal_session_service.read_session(payload.name)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="终端会话不存在",
            ) from exc
        except InvalidTerminalSessionError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="终端会话名无效",
            ) from exc
        return {
            "name": session.name,
            "path": session.path,
            "size": session.size,
            "modified_at": session.modified_at,
            "content": session.content,
        }

    @router.websocket("/connect")
    async def connect_terminal(websocket: WebSocket) -> None:
        if not container.session_codec.verify(websocket.cookies.get(SESSION_COOKIE)):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()

        async def receive_terminal_message() -> dict[str, object]:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                raise InvalidTerminalMessageError("terminal message must be an object")
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
        except InvalidTerminalMessageError:
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)

    return router
