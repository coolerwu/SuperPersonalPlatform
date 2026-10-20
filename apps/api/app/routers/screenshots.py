from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import Session
from app.services.workspace import WorkspaceError, resolve_workspace_path

router = APIRouter(
    prefix="/api/sessions",
    tags=["screenshots"],
    dependencies=[Depends(require_auth)],
)

_ID = re.compile(r"^[0-9a-f]{32}$")


class ScreenshotOut(BaseModel):
    id: str
    run_id: str
    created_at: datetime


async def _session_root(session_id: str, db: AsyncSession) -> Path:
    if not _ID.fullmatch(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        workspace = resolve_workspace_path(session.agent_id, create=False)
    except WorkspaceError:
        raise HTTPException(status_code=404, detail="session workspace not found")
    return workspace / "screenshots" / session_id


@router.get("/{session_id}/screenshots", response_model=list[ScreenshotOut])
async def list_screenshots(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> list[ScreenshotOut]:
    root = await _session_root(session_id, db)
    if not root.is_dir():
        return []
    rows: list[ScreenshotOut] = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir() or not _ID.fullmatch(run_dir.name):
            continue
        for file in run_dir.glob("*.png"):
            if not _ID.fullmatch(file.stem):
                continue
            try:
                stat = file.stat()
            except OSError:
                continue
            rows.append(
                ScreenshotOut(
                    id=file.stem,
                    run_id=run_dir.name,
                    created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
            )
    rows.sort(key=lambda row: (row.created_at, row.id))
    return rows


@router.get("/{session_id}/screenshots/{run_id}/{screenshot_id}.png")
async def get_screenshot(
    session_id: str,
    run_id: str,
    screenshot_id: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    root = await _session_root(session_id, db)
    if not _ID.fullmatch(run_id) or not _ID.fullmatch(screenshot_id):
        raise HTTPException(status_code=404, detail="screenshot not found")
    path = root / run_id / f"{screenshot_id}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )
