from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth import require_auth
from app.config import settings
from app.schemas import AppSettingsUpdate
from app.services.settings_store import SettingsStore, get_settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _settings_out(retention_days: int) -> dict[str, int]:
    return {
        "session_retention_days": int(retention_days),
        "session_cleanup_interval_seconds": max(
            60, settings.session_cleanup_interval_seconds
        ),
    }


@router.get("")
async def get_settings(request: Request) -> dict[str, int]:
    runtime = (await _store(request).get()).runtime
    return _settings_out(runtime.session_retention_days)


@router.put("")
async def update_settings(
    payload: AppSettingsUpdate, request: Request
) -> dict[str, int]:
    store = _store(request)
    await store.update_runtime(session_retention_days=payload.session_retention_days)
    runtime = (await store.get()).runtime
    return _settings_out(runtime.session_retention_days)
