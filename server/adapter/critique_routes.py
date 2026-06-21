from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from server.adapter.auth_routes import SESSION_COOKIE, is_authenticated_request
from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.domain.critique import CritiqueDisciplineNotFoundError, CritiqueRunNotFoundError


class DisciplinePayload(BaseModel):
    name: str
    known_scope: str
    critique_focus: str
    default_enabled: bool = True


def create_critique_router(container: AppContainer) -> APIRouter:
    def require_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(prefix="/api/critique", tags=["critique"])
    service = container.critique_service
    if service is None:
        return router

    @router.get("/disciplines", dependencies=[Depends(require_auth)])
    def list_disciplines() -> dict:
        return {"disciplines": [asdict(item) for item in service.list_disciplines()]}

    @router.post(
        "/disciplines",
        dependencies=[Depends(require_auth)],
        status_code=status.HTTP_201_CREATED,
    )
    def create_discipline(payload: DisciplinePayload) -> dict:
        try:
            discipline = service.create_discipline(
                payload.name,
                payload.known_scope,
                payload.critique_focus,
                payload.default_enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"discipline": asdict(discipline)}

    @router.put("/disciplines/{discipline_id}", dependencies=[Depends(require_auth)])
    def update_discipline(discipline_id: str, payload: DisciplinePayload) -> dict:
        try:
            discipline = service.update_discipline(
                discipline_id,
                name=payload.name,
                known_scope=payload.known_scope,
                critique_focus=payload.critique_focus,
                default_enabled=payload.default_enabled,
            )
        except CritiqueDisciplineNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"discipline": asdict(discipline)}

    @router.delete("/disciplines/{discipline_id}", dependencies=[Depends(require_auth)])
    def delete_discipline(discipline_id: str) -> dict:
        try:
            service.delete_discipline(discipline_id)
        except CritiqueDisciplineNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"ok": True}

    @router.get("/runs", dependencies=[Depends(require_auth)])
    def list_runs() -> dict:
        return {"runs": [asdict(item) for item in service.list_runs()]}

    @router.get("/runs/{run_id}", dependencies=[Depends(require_auth)])
    def get_run(run_id: str) -> dict:
        try:
            run = service.get_run(run_id)
        except CritiqueRunNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"run": asdict(run)}

    @router.websocket("/runs/connect")
    async def connect_runs(websocket: WebSocket) -> None:
        session_cookie = websocket.cookies.get(SESSION_COOKIE)
        if not is_authenticated_request(container, session_cookie):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        await websocket.send_json({"type": "status", "status": "connected"})
        try:
            while True:
                raw = await websocket.receive_json()
                message_type = raw.get("type")
                if message_type not in {"run", "retry"}:
                    await websocket.send_json({"type": "error", "message": "不支持的消息类型"})
                    continue

                async def send_event(event: dict[str, object]) -> None:
                    await websocket.send_json(event)

                try:
                    if message_type == "retry":
                        await service.retry_discipline(
                            str(raw.get("run_id") or ""),
                            str(raw.get("discipline_id") or ""),
                            on_event=send_event,
                        )
                    else:
                        discipline_ids_raw = raw.get("discipline_ids") or []
                        if not isinstance(discipline_ids_raw, list):
                            raise ValueError("discipline_ids 必须是数组")
                        await service.run_critique(
                            str(raw.get("question") or ""),
                            tuple(str(item) for item in discipline_ids_raw),
                            model_id=str(raw.get("model_id") or "").strip() or None,
                            on_event=send_event,
                        )
                except (ValueError, CritiqueDisciplineNotFoundError, CritiqueRunNotFoundError) as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                except Exception:
                    await websocket.send_json({"type": "error", "message": "多维批判运行失败"})
        except WebSocketDisconnect:
            return

    return router
