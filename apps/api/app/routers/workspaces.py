from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.schemas import WorkspaceOut
from app.services.workspace import list_workspace_dirs

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces() -> list[WorkspaceOut]:
    return [WorkspaceOut(**item) for item in list_workspace_dirs()]
