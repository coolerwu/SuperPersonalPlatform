from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import settings
from app.db import get_db
from app.models import AppConfig
from app.schemas import AppSettingsUpdate
from app.services import app_config

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


def _settings_out(row: AppConfig) -> dict[str, int]:
    return {
        "session_retention_days": int(row.session_retention_days),
        "memory_consolidate_every_turns": int(row.memory_consolidate_every_turns),
        "session_cleanup_interval_seconds": max(60, settings.session_cleanup_interval_seconds),
    }


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    return _settings_out(await app_config.get_or_create(db))


@router.put("")
async def update_settings(
    payload: AppSettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    row = await app_config.get_or_create(db)
    row.session_retention_days = app_config.bounded_retention_days(
        payload.session_retention_days
    )
    if payload.memory_consolidate_every_turns is not None:
        row.memory_consolidate_every_turns = app_config.bounded_consolidate_turns(
            payload.memory_consolidate_every_turns
        )
    await db.commit()
    await db.refresh(row)
    return _settings_out(row)
