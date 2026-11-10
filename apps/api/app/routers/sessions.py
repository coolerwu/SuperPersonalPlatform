from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import require_auth
from app.schemas import SessionSummaryOut, TranscriptMessageOut

router = APIRouter(prefix="/api", tags=["sessions"], dependencies=[Depends(require_auth)])


def _catalog(request: Request):
    catalog = getattr(request.app.state, "session_catalog", None)
    if catalog is None:
        raise HTTPException(status_code=503, detail="session catalog is not ready")
    return catalog


@router.get("/agents/{agent_id}/sessions", response_model=list[SessionSummaryOut])
async def list_sessions(agent_id: str, request: Request) -> list[SessionSummaryOut]:
    catalog = _catalog(request)
    registry = getattr(request.app.state, "registry", None)
    rows = await catalog.list_sessions(agent_id)
    return [
        SessionSummaryOut(**row, busy=bool(registry and registry.is_busy(row["id"])))
        for row in rows
    ]


@router.get("/sessions/{session_id}/messages", response_model=list[TranscriptMessageOut])
async def list_messages(
    session_id: str, request: Request, agent_id: str | None = None
) -> list[TranscriptMessageOut]:
    catalog = _catalog(request)
    return [
        TranscriptMessageOut(**row)
        for row in await catalog.messages(session_id, agent_id=agent_id)
    ]


@router.get("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str, request: Request, turn_id: str | None = None
) -> StreamingResponse:
    registry = getattr(request.app.state, "registry", None)
    stream = None
    if registry is not None:
        resolved = turn_id or registry.current_turn_id(session_id)
        stream = registry.stream(resolved) if resolved else None
    if stream is None:
        raise HTTPException(status_code=409, detail="no active turn for session")

    async def generator():
        queue = await stream.subscribe()
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") == "turn_end":
                return

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str, request: Request) -> dict[str, bool]:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    return {"interrupted": await registry.interrupt(session_id)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, agent_id: str, request: Request) -> dict[str, bool]:
    registry = getattr(request.app.state, "registry", None)
    if registry is not None and registry.is_busy(session_id):
        raise HTTPException(status_code=409, detail="session has an active turn")
    catalog = _catalog(request)
    deleted = await catalog.delete(agent_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": True}


@router.post("/sessions/{session_id}/rename")
async def rename_session(
    session_id: str,
    payload: dict[str, str],
    request: Request,
    agent_id: str | None = None,
) -> dict[str, bool]:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        await _catalog(request).rename(session_id, title, agent_id=agent_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return {"renamed": True}
