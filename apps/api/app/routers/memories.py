"""User profile memory backed by ``claude/CLAUDE.md``.

The Claude Agent SDK loads this file as user-level instructions, so the whole
document is the memory. Writes keep the previous version as ``CLAUDE.md.bak``
and replace the file atomically (temporary file, fsync, ``os.replace``). Files
are private because the profile can contain personal facts.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.schemas import MemoryFileOut, MemoryFileUpdate
from app.services.claude_env import claude_paths

router = APIRouter(prefix="/api/memories", tags=["memories"], dependencies=[Depends(require_auth)])


def _memory_path() -> Path:
    return claude_paths()["config"] / "CLAUDE.md"


def _read_content(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except FileNotFoundError:
        return ""


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _backup_and_write(path: Path, content: str) -> None:
    if path.is_file():
        _atomic_write(path.with_name(path.name + ".bak"), path.read_text("utf-8"))
    _atomic_write(path, content)


@router.get("", response_model=MemoryFileOut, response_model_exclude_none=True)
async def read_memory() -> MemoryFileOut:
    return MemoryFileOut(content=await asyncio.to_thread(_read_content, _memory_path()))


@router.put("", response_model=MemoryFileOut)
async def write_memory(payload: MemoryFileUpdate) -> MemoryFileOut:
    await asyncio.to_thread(_backup_and_write, _memory_path(), payload.content)
    return MemoryFileOut(content=payload.content, saved=True)
