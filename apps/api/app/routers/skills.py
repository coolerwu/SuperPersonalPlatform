from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
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
        modified=document.modified,
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
                modified=skill.modified,
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
async def remove_skill(directory: str) -> dict[str, bool]:
    try:
        delete_skill(directory)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"deleted": True}
