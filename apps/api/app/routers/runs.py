from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import SessionLocal, get_db
from app.models import ChannelRunLink, Run, RunEvent, Session, utcnow
from app.schemas import RunEventOut, RunOut
from app.services.run_service import get_run_events, serialize_event

router = APIRouter(prefix="/api/runs", tags=["runs"], dependencies=[Depends(require_auth)])


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)) -> RunOut:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunOut.model_validate(run)


@router.get("/{run_id}/events", response_model=list[RunEventOut])
async def get_events(run_id: str, after: int = Query(default=0, ge=0)) -> list[RunEventOut]:
    events = await get_run_events(run_id, after)
    return [RunEventOut.model_validate(event) for event in events]


@router.get("/{run_id}/stream")
async def stream_events(run_id: str, request: Request, after: int = Query(default=0, ge=0)) -> StreamingResponse:
    async def generator():
        last_id = after
        while True:
            events = await get_run_events(run_id, last_id)
            for event in events:
                last_id = event.id
                payload = serialize_event(event)
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            async with SessionLocal() as db:
                run = await db.get(Run, run_id)
            if run is None:
                yield 'data: {"type":"run_not_found"}\n\n'
                return
            if run.status in ("completed", "failed", "cancelled"):
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/retry")
async def retry_run(run_id: str, request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    original = await db.get(Run, run_id)
    if original is None:
        raise HTTPException(status_code=404, detail="run not found")
    if original.status not in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail="only failed or cancelled runs can be retried")
    active = await db.scalar(
        select(Run)
        .where(Run.session_id == original.session_id, Run.status.in_(("queued", "running")))
        .limit(1)
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "session already has an active run", "active_run_id": active.id},
        )

    retry = Run(
        agent_id=original.agent_id,
        session_id=original.session_id,
        source="retry",
        status="queued",
        input=original.input,
    )
    db.add(retry)
    await db.flush()
    link = await db.get(ChannelRunLink, original.id)
    if link is not None:
        # A retried WeChat Run still owns a reply back to that conversation.
        db.add(
            ChannelRunLink(
                run_id=retry.id,
                channel_id=link.channel_id,
                conversation_id=link.conversation_id,
            )
        )
    db.add(
        RunEvent(
            run_id=retry.id,
            type="run_queued",
            payload={"content": original.input, "retry_of": original.id},
        )
    )
    session = await db.get(Session, original.session_id)
    if session is not None:
        session.updated_at = utcnow()
    await db.commit()
    await db.refresh(retry)

    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.wake()
    return {"run_id": retry.id}


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, bool]:
    worker = getattr(request.app.state, "worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="run worker is not running")
    try:
        cancelled = await worker.cancel(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return {"cancelled": cancelled}
