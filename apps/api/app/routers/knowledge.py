from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.config import settings
from app.schemas import KnowledgeConfigUpdate
from app.services.knowledge_sync import MIN_SYNC_INTERVAL_SECONDS
from app.services.settings_store import KnowledgeSection, SettingsStore, get_settings_store

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(require_auth)])


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _config_out(section: KnowledgeSection | None) -> dict[str, object]:
    interval = settings.kb_sync_interval_seconds
    if section is not None:
        interval = section.interval_seconds or interval
    return {
        "enabled": bool(section.enabled) if section is not None else False,
        "webdav_url": (section.url if section is not None else "") or "",
        "username": (section.username if section is not None else "") or "",
        "root": ((section.root if section is not None else "") or "").strip().strip("/"),
        "interval_seconds": max(MIN_SYNC_INTERVAL_SECONDS, interval),
        "has_password": bool(section is not None and section.password),
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


@router.get("")
async def get_knowledge(request: Request) -> dict[str, object]:
    section = (await _store(request).get()).knowledge
    return {"config": _config_out(section), "status": _runtime_status(request)}


@router.put("")
async def update_knowledge(
    payload: KnowledgeConfigUpdate, request: Request
) -> dict[str, object]:
    store = _store(request)
    data = payload.model_dump(exclude_unset=True)
    fields: dict[str, object] = {}

    if "enabled" in data and data["enabled"] is not None:
        fields["enabled"] = bool(data["enabled"])
    if "webdav_url" in data and data["webdav_url"] is not None:
        fields["url"] = _validate_webdav_url(str(data["webdav_url"]))
    if "username" in data and data["username"] is not None:
        fields["username"] = str(data["username"]).strip()
    if "root" in data and data["root"] is not None:
        fields["root"] = str(data["root"]).strip().strip("/")
    if "interval_seconds" in data and data["interval_seconds"] is not None:
        fields["interval_seconds"] = max(
            MIN_SYNC_INTERVAL_SECONDS, int(data["interval_seconds"])
        )
    if data.get("clear_password"):
        fields["password"] = ""
    if "password" in data and data["password"] and str(data["password"]).strip():
        # A blank password keeps the stored one; clear_password removes it.
        fields["password"] = str(data["password"]).strip()

    if fields:
        await store.update_knowledge(**fields)

    runtime = getattr(request.app.state, "knowledge", None)
    if runtime is not None:
        runtime.wake(force=True)
    return {"config": _config_out((await store.get()).knowledge), "status": _runtime_status(request)}


@router.post("/sync")
async def sync_knowledge(request: Request) -> dict[str, object]:
    section = (await _store(request).get()).knowledge
    if not section.enabled or not (section.url or "").strip():
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
