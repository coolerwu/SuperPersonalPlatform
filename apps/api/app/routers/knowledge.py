from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import settings
from app.db import get_db
from app.models import KnowledgeConfig
from app.schemas import KnowledgeConfigUpdate
from app.services.knowledge_sync import KNOWLEDGE_CONFIG_ID, MIN_SYNC_INTERVAL_SECONDS

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(require_auth)])


def _config_out(row: KnowledgeConfig | None) -> dict[str, object]:
    interval = settings.kb_sync_interval_seconds
    if row is not None:
        interval = row.interval_seconds or interval
    return {
        "enabled": bool(row.enabled) if row is not None else False,
        "webdav_url": (row.webdav_url if row is not None else "") or "",
        "username": (row.username if row is not None else "") or "",
        "root": (row.root if row is not None else "") or "",
        "interval_seconds": max(MIN_SYNC_INTERVAL_SECONDS, interval),
        "has_password": bool(row is not None and row.password),
        "local_dir": str(settings.knowledge_dir),
    }


def _runtime_status(request: Request) -> dict[str, object]:
    runtime = getattr(request.app.state, "knowledge", None)
    if runtime is not None:
        return runtime.status()
    return {
        "enabled": False,
        "configured": False,
        "blocked": False,
        "syncing": False,
        "retry_at": None,
        "local_dir": str(settings.knowledge_dir),
        "interval_seconds": max(MIN_SYNC_INTERVAL_SECONDS, settings.kb_sync_interval_seconds),
        "last_sync_at": None,
        "last_error": "",
        "last_result": None,
    }


def _validate_webdav_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(status_code=400, detail="WebDAV 地址必须是完整的 http/https URL")
    return value


async def _get_or_create(db: AsyncSession) -> KnowledgeConfig:
    row = await db.get(KnowledgeConfig, KNOWLEDGE_CONFIG_ID)
    if row is None:
        row = KnowledgeConfig(
            id=KNOWLEDGE_CONFIG_ID,
            interval_seconds=max(MIN_SYNC_INTERVAL_SECONDS, settings.kb_sync_interval_seconds),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


@router.get("")
async def get_knowledge(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    row = await db.get(KnowledgeConfig, KNOWLEDGE_CONFIG_ID)
    return {"config": _config_out(row), "status": _runtime_status(request)}


@router.put("")
async def update_knowledge(
    payload: KnowledgeConfigUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    row = await _get_or_create(db)
    data = payload.model_dump(exclude_unset=True)

    if "enabled" in data and data["enabled"] is not None:
        row.enabled = bool(data["enabled"])
    if "webdav_url" in data and data["webdav_url"] is not None:
        row.webdav_url = _validate_webdav_url(str(data["webdav_url"]))
    if "username" in data and data["username"] is not None:
        row.username = str(data["username"]).strip()
    if "root" in data and data["root"] is not None:
        row.root = str(data["root"]).strip().strip("/")
    if "interval_seconds" in data and data["interval_seconds"] is not None:
        row.interval_seconds = int(data["interval_seconds"])
    if data.get("clear_password"):
        row.password = ""
    if "password" in data and data["password"] and str(data["password"]).strip():
        # A blank password keeps the stored one; clear_password removes it.
        row.password = str(data["password"]).strip()

    await db.commit()
    await db.refresh(row)

    runtime = getattr(request.app.state, "knowledge", None)
    if runtime is not None:
        runtime.wake(force=True)
    return {"config": _config_out(row), "status": _runtime_status(request)}


@router.post("/sync")
async def sync_knowledge(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    row = await db.get(KnowledgeConfig, KNOWLEDGE_CONFIG_ID)
    if row is None or not row.enabled or not (row.webdav_url or "").strip():
        raise HTTPException(status_code=400, detail="知识库尚未配置或已停用")
    runtime = getattr(request.app.state, "knowledge", None)
    if runtime is not None:
        status = runtime.status()
        if status.get("blocked"):
            raise HTTPException(
                status_code=429,
                detail="坚果云暂时限制了 WebDAV 访问，请等待限流解除后再试",
            )
        runtime.wake()
    return {"queued": True, "status": _runtime_status(request)}
