from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import Agent
from app.schemas import MemoryCreate, MemoryOut, MemoryUpdate
from app.services import memory_service

router = APIRouter(prefix="/api/memories", tags=["memories"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    scope: str | None = Query(default=None, pattern="^(user|agent)$"),
    agent_id: str | None = None,
    q: str | None = None,
) -> list[MemoryOut]:
    items = await memory_service.list_memories(scope=scope, agent_id=agent_id, query=q)
    return [MemoryOut.model_validate(item) for item in items]


@router.post("", response_model=MemoryOut, status_code=201)
async def create_memory(
    payload: MemoryCreate,
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    if payload.scope == "agent" and not payload.agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required for agent memory")
    if payload.scope == "agent" and await db.get(Agent, payload.agent_id) is None:
        raise HTTPException(status_code=404, detail="agent not found")
    item = await memory_service.create_memory(
        scope=payload.scope,
        agent_id=payload.agent_id,
        content=payload.content,
        tags=payload.tags,
    )
    if item is None:
        raise HTTPException(status_code=400, detail="memory content is empty or limit reached")
    return MemoryOut.model_validate(item)


@router.put("/{memory_id}", response_model=MemoryOut)
async def update_memory(memory_id: str, payload: MemoryUpdate) -> MemoryOut:
    item = await memory_service.update_memory(
        memory_id, content=payload.content, tags=payload.tags
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
