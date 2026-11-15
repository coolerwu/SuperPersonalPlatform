from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import MapsConfigUpdate
from app.services.settings_store import (
    MapsSection,
    SettingsStore,
    get_settings_store,
)

router = APIRouter(
    prefix="/api/maps", tags=["maps"], dependencies=[Depends(require_auth)]
)


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _config_out(section: MapsSection) -> dict[str, object]:
    return {
        "enabled": bool(section.enabled),
        "provider": section.provider or "amap",
        "has_api_key": bool(section.api_key),
    }


@router.get("")
async def get_maps(request: Request) -> dict[str, object]:
    return _config_out((await _store(request).get()).maps)


@router.put("")
async def update_maps(
    payload: MapsConfigUpdate, request: Request
) -> dict[str, object]:
    store = _store(request)
    current = (await store.get()).maps
    fields: dict[str, object] = {}

    if payload.clear_api_key:
        fields["api_key"] = ""
        fields["enabled"] = False
    else:
        if payload.api_key is not None and payload.api_key.strip():
            fields["api_key"] = payload.api_key.strip()
        if payload.enabled is not None:
            fields["enabled"] = bool(payload.enabled)
        next_key = str(fields.get("api_key", current.api_key) or "").strip()
        next_enabled = bool(fields.get("enabled", current.enabled))
        if next_enabled and not next_key:
            raise HTTPException(
                status_code=400, detail="请先填写高德 Web 服务 Key"
            )

    if fields:
        await store.update_maps(**fields)
    return _config_out((await store.get()).maps)
