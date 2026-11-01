from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import Agent
from app.schemas import SkillCatalogOut, SkillDetailOut, SkillOut, SkillUpdate
from app.services.skills import (
    SkillDocument,
    SkillNotFoundError,
    SkillValidationError,
    delete_skill,
    read_skill,
    scan_skills,
    update_skill,
)

router = APIRouter(prefix="/api/skills", tags=["skills"], dependencies=[Depends(require_auth)])


def _detail_out(document: SkillDocument) -> SkillDetailOut:
    return SkillDetailOut(
        name=document.name,
        directory=document.directory,
        path=document.path,
        content=document.content,
        warnings=list(document.warnings),
        source=document.source,
    )


@router.get("", response_model=SkillCatalogOut)
async def list_skills() -> SkillCatalogOut:
    catalog = scan_skills()
    return SkillCatalogOut(
        skills=[
            SkillOut(
                name=skill.name,
                description=skill.description,
                path=skill.path,
                directory=skill.directory,
                include_tools=list(skill.include_tools),
                can_enable=skill.can_enable,
                warnings=list(skill.warnings),
                source=skill.source,
            )
            for skill in catalog.skills
        ],
        errors=list(catalog.errors),
    )


@router.get("/{directory}", response_model=SkillDetailOut)
async def get_skill(directory: str) -> SkillDetailOut:
    try:
        return _detail_out(read_skill(directory))
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{directory}", response_model=SkillDetailOut)
async def save_skill(directory: str, payload: SkillUpdate) -> SkillDetailOut:
    try:
        return _detail_out(update_skill(directory, payload.content))
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{directory}")
async def remove_skill(
    directory: str, db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    try:
        declared_name = delete_skill(directory)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    removed = {directory, declared_name}
    agents = (await db.scalars(select(Agent))).all()
    changed = False
    for agent in agents:
        names = list(agent.enabled_skills or [])
        if any(name in removed for name in names):
            agent.enabled_skills = [name for name in names if name not in removed]
            changed = True
    if changed:
        await db.commit()
    return {"deleted": True}
