from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import SkillCatalogOut, SkillDetailOut, SkillOut, SkillUpdate
from app.services.settings_store import SettingsStore, get_settings_store
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


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


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
async def remove_skill(directory: str, request: Request) -> dict[str, bool]:
    try:
        declared_name = delete_skill(directory)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    removed = {directory, declared_name}

    def drop_from_agents(data: dict) -> None:
        agents = data.get("agents")
        if not isinstance(agents, list):
            return
        for item in agents:
            if not isinstance(item, dict):
                continue
            names = item.get("enabled_skills")
            if isinstance(names, list):
                item["enabled_skills"] = [
                    name for name in names if name not in removed
                ]

    await _store(request).mutate_raw(drop_from_agents)
    return {"deleted": True}
