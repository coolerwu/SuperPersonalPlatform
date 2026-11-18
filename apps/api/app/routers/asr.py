from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.auth import require_auth
from app.schemas import AsrConfigUpdate
from app.services.asr import (
    MAX_SECONDS,
    MAX_UPLOAD_BYTES,
    MAX_WAV_BYTES,
    AsrError,
    transcode_to_wav,
    transcribe_wav,
    wav_duration_seconds,
)
from app.services.settings_store import (
    AsrSection,
    SettingsStore,
    get_settings_store,
)

router = APIRouter(
    prefix="/api/asr", tags=["asr"], dependencies=[Depends(require_auth)]
)


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _config_out(section: AsrSection) -> dict[str, object]:
    return {
        "enabled": bool(section.enabled),
        "has_api_key": bool((section.api_key or "").strip()),
    }


@router.get("")
async def get_asr(request: Request) -> dict[str, object]:
    return _config_out((await _store(request).get()).asr)


@router.put("")
async def update_asr(
    payload: AsrConfigUpdate, request: Request
) -> dict[str, object]:
    try:
        section = await _store(request).update_asr(
            enabled=payload.enabled,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _config_out(section)


@router.post("/transcribe")
async def transcribe_audio(
    request: Request, file: UploadFile = File(...)
) -> dict[str, object]:
    section = (await _store(request).get()).asr
    if not section.enabled or not (section.api_key or "").strip():
        raise HTTPException(
            status_code=409,
            detail="语音识别未启用，请先在配置页填写 DeepSeek API Key",
        )
    source = await file.read()
    if len(source) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="录音文件过大")
    try:
        wav = await transcode_to_wav(source)
        if wav_duration_seconds(wav) > MAX_SECONDS:
            raise AsrError(400, "录音超过 2 分钟")
        if len(wav) > MAX_WAV_BYTES:
            raise AsrError(413, "录音文件过大")
        client = getattr(request.app.state, "asr_client", None)
        return await transcribe_wav(wav, section.api_key.strip(), client=client)
    except AsrError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
