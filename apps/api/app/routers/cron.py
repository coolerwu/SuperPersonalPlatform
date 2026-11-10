"""Cron job management endpoints (single-user cookie auth).

Jobs submit turns to the SDK ``SessionRegistry``; there are no Run or Session
rows behind them. Agent configuration and channel bindings come from the
settings store.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import CronJob
from app.schemas import CronJobCreate, CronJobOut, CronJobUpdate
from app.services import cron_service
from app.services.cron_service import (
    CronBusy,
    CronConflict,
    CronInactiveAgent,
    CronNotFound,
    CronTriggerFailed,
    CronValidationError,
    run_job_now,
)
from app.services.identifiers import valid_agent_id

router = APIRouter(prefix="/api/cron-jobs", tags=["cron"], dependencies=[Depends(require_auth)])


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "settings_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="settings store is not running")
    return store


def _registry(request: Request) -> Any:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    return registry


def _available_agent(settings: Any, agent_id: str) -> Any:
    """An Agent that may still own cron jobs (paused is fine, archived is not)."""
    if not valid_agent_id(agent_id):
        raise HTTPException(status_code=404, detail="agent not found")
    agent = next((item for item in settings.agents if item.id == agent_id), None)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


def _active_agent(settings: Any, agent_id: str) -> Any:
    """An Agent that may create jobs right now (paused is a 400, not hidden)."""
    agent = _available_agent(settings, agent_id)
    if agent.status != "active":
        raise HTTPException(status_code=400, detail=f"agent is {agent.status}")
    return agent


def _job_out(job: CronJob, settings: Any) -> CronJobOut:
    push_channel_name = ""
    if job.push_channel_id:
        channel = next(
            (item for item in settings.channels if item.id == job.push_channel_id),
            None,
        )
        push_channel_name = channel.name if channel is not None else ""
    push_conversation_name = ""
    if job.push_channel_id and job.push_conversation_id:
        conversation = settings.channel_conversations.get(
            f"{job.push_channel_id}:{job.push_conversation_id}"
        )
        if conversation is not None:
            push_conversation_name = conversation.display_name
    return CronJobOut(
        id=job.id,
        agent_id=job.agent_id,
        name=job.name,
        prompt=job.prompt,
        schedule=job.schedule,
        timezone=job.timezone,
        enabled=job.enabled,
        session_id=job.session_id,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        last_skipped_at=job.last_skipped_at,
        push_channel_id=job.push_channel_id,
        push_conversation_id=job.push_conversation_id,
        push_channel_name=push_channel_name,
        push_conversation_name=push_conversation_name,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("", response_model=list[CronJobOut])
async def list_cron_jobs(
    request: Request,
    agent_id: str = Query(..., min_length=1, max_length=32),
    db: AsyncSession = Depends(get_db),
) -> list[CronJobOut]:
    settings = await _store(request).get()
    _available_agent(settings, agent_id)
    jobs = await cron_service.list_jobs(db, agent_id)
    return [_job_out(job, settings) for job in jobs]


@router.post("", response_model=CronJobOut, status_code=201)
async def create_cron_job(
    payload: CronJobCreate, request: Request, db: AsyncSession = Depends(get_db)
) -> CronJobOut:
    store = _store(request)
    settings = await store.get()
    _active_agent(settings, payload.agent_id)
    try:
        job = await cron_service.create_job(
            db,
            agent_id=payload.agent_id,
            name=payload.name,
            prompt=payload.prompt,
            schedule=payload.schedule,
            timezone=payload.timezone,
            enabled=payload.enabled,
            push_channel_id=payload.push_channel_id,
            push_conversation_id=payload.push_conversation_id,
            store=store,
        )
    except CronNotFound as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    except CronInactiveAgent as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CronValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CronConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(job)
    return _job_out(job, settings)


@router.put("/{job_id}", response_model=CronJobOut)
async def update_cron_job(
    job_id: str, payload: CronJobUpdate, request: Request, db: AsyncSession = Depends(get_db)
) -> CronJobOut:
    job = await db.get(CronJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="cron job not found")
    store = _store(request)
    settings = await store.get()
    _available_agent(settings, job.agent_id)
    try:
        job = await cron_service.update_job(
            db,
            job_id,
            agent_id=job.agent_id,
            name=payload.name,
            prompt=payload.prompt,
            schedule=payload.schedule,
            timezone=payload.timezone,
            enabled=payload.enabled,
            push_channel_id=payload.push_channel_id,
            push_conversation_id=payload.push_conversation_id,
            store=store,
        )
    except CronNotFound as exc:
        raise HTTPException(status_code=404, detail="cron job not found") from exc
    except CronValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CronConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(job)
    return _job_out(job, settings)


@router.delete("/{job_id}", response_model=CronJobOut)
async def delete_cron_job(
    job_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> CronJobOut:
    job = await db.get(CronJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="cron job not found")
    settings = await _store(request).get()
    _available_agent(settings, job.agent_id)
    try:
        job = await cron_service.delete_job(db, job_id, agent_id=job.agent_id)
    except CronNotFound as exc:
        raise HTTPException(status_code=404, detail="cron job not found") from exc
    output = _job_out(job, settings)
    await db.commit()
    return output


@router.post("/{job_id}/run", response_model=CronJobOut, status_code=201)
async def run_cron_job_now(
    job_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> CronJobOut:
    job = await db.get(CronJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="cron job not found")
    store = _store(request)
    settings = await store.get()
    registry = _registry(request)
    try:
        # The service is the single source of truth for Agent state: paused
        # Agents raise CronInactiveAgent here.
        job = await run_job_now(job_id, store=store, registry=registry)
    except CronNotFound as exc:
        raise HTTPException(status_code=404, detail="cron job not found") from exc
    except CronInactiveAgent as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CronBusy as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "任务会话已有运行中的任务，请稍后再试",
                "status": exc.status,
            },
        ) from exc
    except CronTriggerFailed as exc:
        raise HTTPException(
            status_code=503, detail="任务触发失败，请稍后重试"
        ) from exc
    return _job_out(job, settings)
