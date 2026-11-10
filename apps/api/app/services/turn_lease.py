from __future__ import annotations

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import RunningSession


async def add(session_id: str) -> None:
    async with SessionLocal() as db:
        db.add(RunningSession(session_id=session_id))
        try:
            await db.commit()
        except Exception:
            await db.rollback()


async def remove(session_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(RunningSession).where(RunningSession.session_id == session_id)
        )
        await db.commit()


async def list_all() -> list[str]:
    async with SessionLocal() as db:
        rows = await db.scalars(select(RunningSession.session_id))
        return [str(row) for row in rows]


async def is_running(session_id: str) -> bool:
    async with SessionLocal() as db:
        row = await db.get(RunningSession, session_id)
        return row is not None
