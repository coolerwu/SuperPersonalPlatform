from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.schemas import SkillCatalogOut, SkillOut
from app.services.skills import scan_skills

router = APIRouter(prefix="/api/skills", tags=["skills"], dependencies=[Depends(require_auth)])


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
            )
            for skill in catalog.skills
        ],
        errors=list(catalog.errors),
    )
