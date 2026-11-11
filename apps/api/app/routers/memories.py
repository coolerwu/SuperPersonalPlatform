"""Memory documents exposed by the memory page.

The shared user profile is the user-level ``claude/CLAUDE.md``. Each Agent also
has a project-level ``workspaces/<agent_id>/CLAUDE.md`` loaded as project memory,
plus Claude's own read-only auto memory under ``claude/projects/<agent_id>/memory``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import (
    AgentAutoMemoryOut,
    AutoMemoryFileOut,
    MemoryFileOut,
    MemoryFileUpdate,
)
from app.services import memories as memory_service
from app.services.memories import AgentNotFoundError, MemoryPathError
from app.services.settings_store import SettingsStore, get_settings_store

router = APIRouter(
    prefix="/api/memories", tags=["memories"], dependencies=[Depends(require_auth)]
)


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


async def _require_agent(request: Request, agent_id: str) -> None:
    try:
        await memory_service.require_agent(_store(request), agent_id)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc


def _read(path: Path) -> str:
    return memory_service.read_document(path)


def _write(path: Path, content: str) -> None:
    memory_service.write_document(path, content)


async def _read_document(path: Path) -> str:
    try:
        return await asyncio.to_thread(_read, path)
    except MemoryPathError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _write_document(path: Path, content: str) -> None:
    try:
        await asyncio.to_thread(_write, path, content)
    except MemoryPathError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=MemoryFileOut, response_model_exclude_none=True)
async def read_memory() -> MemoryFileOut:
    path = memory_service.user_memory_path()
    return MemoryFileOut(content=await _read_document(path))


@router.put("", response_model=MemoryFileOut)
async def write_memory(payload: MemoryFileUpdate) -> MemoryFileOut:
    path = memory_service.user_memory_path()
    await _write_document(path, payload.content)
    return MemoryFileOut(content=payload.content, saved=True)


@router.get(
    "/agents/{agent_id}",
    response_model=MemoryFileOut,
    response_model_exclude_none=True,
)
async def read_agent_memory(agent_id: str, request: Request) -> MemoryFileOut:
    await _require_agent(request, agent_id)
    path = memory_service.agent_memory_path(agent_id)
    return MemoryFileOut(content=await _read_document(path))


@router.put("/agents/{agent_id}", response_model=MemoryFileOut)
async def write_agent_memory(
    agent_id: str, payload: MemoryFileUpdate, request: Request
) -> MemoryFileOut:
    await _require_agent(request, agent_id)
    path = memory_service.agent_memory_path(agent_id)
    await _write_document(path, payload.content)
    return MemoryFileOut(content=payload.content, saved=True)


@router.get(
    "/agents/{agent_id}/auto-memory", response_model=AgentAutoMemoryOut
)
async def read_agent_auto_memory(
    agent_id: str, request: Request
) -> AgentAutoMemoryOut:
    await _require_agent(request, agent_id)
    catalog = await asyncio.to_thread(
        memory_service.read_auto_memory, agent_id
    )
    return AgentAutoMemoryOut(
        directory=catalog.directory,
        files=[
            AutoMemoryFileOut(
                name=item.name,
                content=item.content,
                size=item.size,
                updated_at=item.updated_at,
            )
            for item in catalog.files
        ],
        errors=list(catalog.errors),
    )
