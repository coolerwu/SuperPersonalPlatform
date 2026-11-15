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
        "provider": section.provider,
        "has_api_key": bool((section.api_key or "").strip()),
    }


@router.get("")
async def get_maps(request: Request) -> dict[str, object]:
    return _config_out((await _store(request).get()).maps)


@router.put("")
async def update_maps(
    payload: MapsConfigUpdate, request: Request
) -> dict[str, object]:
    try:
        section = await _store(request).update_maps(
            enabled=payload.enabled,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _config_out(section)
