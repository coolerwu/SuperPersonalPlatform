from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.domain.sessions import ChatSessionNotFoundError


class CreateSessionPayload(BaseModel):
    agent_id: str
    title: str = ""


class UpdateSessionPayload(BaseModel):
    title: str | None = None


def _session_to_response(session) -> dict:
    return {
        "id": session.id,
        "title": session.title,
        "agent_id": session.agent_id,
        "message_count": len(session.messages),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "images": [{"mime_type": img.mime_type, "data": img.data} for img in msg.images],
                "checkpoints": [
                    {"stage": cp.stage, "title": cp.title, "detail": cp.detail}
                    for cp in msg.checkpoints
                ],
                "created_at": msg.created_at,
            }
            for msg in session.messages
        ],
    }


def _summary_to_response(s) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "agent_id": s.agent_id,
        "message_count": s.message_count,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def create_session_router(container: AppContainer) -> APIRouter:
    def require_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(prefix="/api/sessions", tags=["sessions"])
    service = container.chat_session_service
    if service is None:
        return router

    @router.get("", dependencies=[Depends(require_auth)])
    def list_sessions() -> dict:
        sessions = service.list_sessions()
        return {"sessions": [_summary_to_response(s) for s in sessions]}

    @router.post("", dependencies=[Depends(require_auth)])
    def create_session(payload: CreateSessionPayload) -> dict:
        if not payload.agent_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="agent_id is required",
            )
        session = service.create_session(
            agent_id=payload.agent_id.strip(),
            title=payload.title.strip(),
        )
        return {"session": _session_to_response(session)}

    @router.get("/{session_id}", dependencies=[Depends(require_auth)])
    def get_session(session_id: str) -> dict:
        try:
            session = service.get_session(session_id)
        except ChatSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return {"session": _session_to_response(session)}

    @router.put("/{session_id}", dependencies=[Depends(require_auth)])
    def update_session(session_id: str, payload: UpdateSessionPayload) -> dict:
        try:
            if payload.title is not None:
                session = service.update_title(session_id, payload.title)
            else:
                session = service.get_session(session_id)
        except ChatSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return {"session": _session_to_response(session)}

    @router.delete("/{session_id}", dependencies=[Depends(require_auth)])
    def delete_session(session_id: str) -> dict:
        try:
            service.delete_session(session_id)
        except ChatSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return {"ok": True}

    return router
