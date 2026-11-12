from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import AppSettingsUpdate
from app.services.claude_settings import ClaudeSettingsError, sync_cleanup_period
from app.services.settings_store import RuntimeSection, SettingsStore, get_settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _settings_out(runtime: RuntimeSection) -> dict[str, int]:
    return {
        "session_retention_days": runtime.session_retention_days,
        "task_budget_tokens": runtime.task_budget_tokens,
    }


@router.get("")
async def get_settings(request: Request) -> dict[str, int]:
    runtime = (await _store(request).get()).runtime
    return _settings_out(runtime)


@router.put("")
async def update_settings(
    payload: AppSettingsUpdate, request: Request
) -> dict[str, int]:
    store = _store(request)
    await store.update_runtime(
        session_retention_days=payload.session_retention_days,
        task_budget_tokens=payload.task_budget_tokens,
    )
    runtime = (await store.get()).runtime
    try:
        await sync_cleanup_period(runtime.session_retention_days)
    except ClaudeSettingsError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"设置已保存，但写入 Claude 设置失败：{exc}",
        ) from exc
    return _settings_out(runtime)
