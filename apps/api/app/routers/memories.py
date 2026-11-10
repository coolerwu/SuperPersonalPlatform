from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import Agent, Message, Session
from app.schemas import MemoryCreate, MemoryOut, MemoryUpdate
from app.services import memory_service

router = APIRouter(prefix="/api/memories", tags=["memories"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    scope: str | None = Query(default=None, pattern="^(user|agent)$"),
    agent_id: str | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[MemoryOut]:
    items = await memory_service.list_memories(scope=scope, agent_id=agent_id, query=q)
    if not items:
        return []

    message_ids = {item.source_message_id for item in items if item.source_message_id}
    session_ids = {item.source_session_id for item in items if item.source_session_id}
    messages: dict[str, str] = {}
    sessions: dict[str, str | None] = {}
    if message_ids:
        rows = await db.execute(
            select(Message.id, Message.session_id).where(Message.id.in_(message_ids))
        )
        messages = {str(message_id): str(session_id) for message_id, session_id in rows}
        session_ids |= set(messages.values())
    if session_ids:
        rows = await db.execute(
            select(Session.id, Session.agent_id).where(Session.id.in_(session_ids))
        )
        sessions = {str(session_id): agent_id for session_id, agent_id in rows}

    result: list[MemoryOut] = []
    for item in items:
        session_id = item.source_session_id or messages.get(item.source_message_id or "")
        result.append(
            MemoryOut.model_validate(item).model_copy(
                update={
                    "source_message_exists": bool(
                        item.source_message_id and item.source_message_id in messages
                    ),
                    "source_session_exists": bool(session_id and session_id in sessions),
                    "source_agent_id": sessions.get(session_id) if session_id else None,
                }
            )
        )
    return result


@router.post("", response_model=MemoryOut, status_code=201)
async def create_memory(
    payload: MemoryCreate,
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    if payload.scope == "agent" and not payload.agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required for agent memory")
    if payload.scope == "agent" and await db.get(Agent, payload.agent_id) is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if (
        payload.scope == "agent"
        and len(memory_service.normalize_content(payload.content))
        > memory_service.MAX_AGENT_MEMORY_CHARS
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "agent memory must be at most "
                f"{memory_service.MAX_AGENT_MEMORY_CHARS} characters"
            ),
        )
    item = await memory_service.create_memory(
        scope=payload.scope,
        agent_id=payload.agent_id,
        content=payload.content,
        tags=payload.tags,
        source_kind="manual",
    )
    if item is None:
        raise HTTPException(status_code=400, detail="memory content is empty or limit reached")
    return MemoryOut.model_validate(item)


@router.put("/{memory_id}", response_model=MemoryOut)
async def update_memory(memory_id: str, payload: MemoryUpdate) -> MemoryOut:
    current = await memory_service.get_memory(memory_id)
    if current is None:
        raise HTTPException(status_code=404, detail="memory not found")
    normalized = memory_service.normalize_content(payload.content or "")
    # A rewritten memory is no longer the user's original words, so its
    # snapshot stops being evidence and the entry becomes a manual note.
    content_changed = bool(
        payload.content is not None and normalized and normalized != current.content
    )
    if (
        content_changed
        and current.scope == "agent"
        and len(normalized) > memory_service.MAX_AGENT_MEMORY_CHARS
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "agent memory must be at most "
                f"{memory_service.MAX_AGENT_MEMORY_CHARS} characters"
            ),
        )
    item = await memory_service.update_memory(
        memory_id,
        content=payload.content,
        tags=payload.tags,
        source_kind="manual" if content_changed else None,
        clear_snapshot=content_changed,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryOut.model_validate(item)


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str) -> dict[str, bool]:
    deleted = await memory_service.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"deleted": True}
