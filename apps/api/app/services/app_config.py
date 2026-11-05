from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import AppConfig

APP_CONFIG_ID = "default"
MIN_SESSION_RETENTION_DAYS = 0
MAX_SESSION_RETENTION_DAYS = 3650


def bounded_retention_days(value: int) -> int:
    return max(MIN_SESSION_RETENTION_DAYS, min(int(value), MAX_SESSION_RETENTION_DAYS))


async def get_or_create(db: AsyncSession) -> AppConfig:
    row = await db.get(AppConfig, APP_CONFIG_ID)
    if row is None:
        row = AppConfig(
            id=APP_CONFIG_ID,
            session_retention_days=bounded_retention_days(
                settings.session_retention_days
            ),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def get_session_retention_days() -> int:
    async with SessionLocal() as db:
        row = await get_or_create(db)
        return int(row.session_retention_days)
