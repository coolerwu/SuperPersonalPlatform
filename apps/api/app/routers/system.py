from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import APP_VERSION
from app.auth import require_auth
from app.config import settings
from app.db import get_db
from app.models import Agent, Message, Provider, Run, Session

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_auth)])


def database_kind(url: str) -> str:
    scheme = url.split(":", 1)[0]
    return scheme.split("+", 1)[0]


async def _count(db: AsyncSession, model: type, *conditions: object) -> int:
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int(await db.scalar(statement) or 0)


@router.get("")
async def system_info(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    return {
        "version": APP_VERSION,
        "execution_core": "deepagents",
        "database": database_kind(settings.app_database_url),
        "run_timeout_seconds": settings.run_timeout_seconds,
        "max_history_messages": settings.max_history_messages,
        "counts": {
            "providers": await _count(db, Provider),
            "agents": await _count(db, Agent, Agent.status != "archived"),
            "sessions": await _count(db, Session),
            "messages": await _count(db, Message),
            "runs": await _count(db, Run),
        },
        "capabilities": {
            "chat": True,
            "multi_agent": True,
            "subagent": True,
            "checkpoints": True,
            "filesystem": False,
            "shell": False,
            "browser": False,
            "skills": False,
            "memory": True,
        },
    }
