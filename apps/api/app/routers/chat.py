from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import ChatRequest, ChatResponse
from app.services import memory_service
from app.services.run_service import ActiveRunConflict, append_event, create_run

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_auth)])


@router.post("/messages", response_model=ChatResponse, status_code=201)
async def send_message(payload: ChatRequest, request: Request) -> ChatResponse:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    try:
        run, message = await create_run(payload.session_id, content)
    except ActiveRunConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "session already has an active run", "active_run_id": exc.run_id, "status": exc.status},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    explicit = memory_service.explicit_memory_content(content)
    if explicit:
        memory = await memory_service.create_memory(
            scope="user",
            content=explicit,
            source_message_id=message.id,
            source_run_id=run.id,
        )
        if memory is not None:
            await append_event(
                run.id,
                "memory_saved",
                {
                    "source": "explicit",
                    "scope": "user",
                    "memory_id": memory.id,
                    "content": memory.content,
                },
            )

    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.wake()
    return ChatResponse(run_id=run.id, session_id=run.session_id, message_id=message.id)
