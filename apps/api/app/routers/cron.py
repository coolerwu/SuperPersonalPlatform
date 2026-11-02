"""Cron job management endpoints (single-user cookie auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import Agent, Channel, ChannelConversation, CronJob, Run
from app.schemas import CronJobCreate, CronJobOut, CronJobUpdate, CronRunOut
from app.services import cron_service
from app.services.cron_service import (
    CronBusy,
    CronConflict,
    CronInactiveAgent,
    CronNotFound,
    CronValidationError,
    run_job_now,
)
from app.services.run_service import ACTIVE_RUN_STATUSES

router = APIRouter(prefix="/api/cron-jobs", tags=["cron"], dependencies=[Depends(require_auth)])


async def _job_out(
    db: AsyncSession,
    job: CronJob,
    status_by_id: dict[str, str] | None = None,
    channel_names: dict[str, str] | None = None,
    conversation_names: dict[str, str] | None = None,
) -> CronJobOut:
    last_status = None
    if job.last_run_id:
        if status_by_id is not None:
            last_status = status_by_id.get(job.last_run_id)
        else:
            last_status = await db.scalar(
                select(Run.status).where(Run.id == job.last_run_id)
            )
    push_channel_name = ""
    if job.push_channel_id:
        if channel_names is not None:
            push_channel_name = channel_names.get(job.push_channel_id, "")
        else:
            push_channel_name = (
                await db.scalar(
                    select(Channel.name).where(Channel.id == job.push_channel_id)
                )
                or ""
            )
    push_conversation_name = ""
    if job.push_conversation_id:
        if conversation_names is not None:
            push_conversation_name = conversation_names.get(
                job.push_conversation_id, ""
            )
        else:
            push_conversation_name = (
                await db.scalar(
                    select(ChannelConversation.display_name).where(
                        ChannelConversation.id == job.push_conversation_id
                    )
                )
                or ""
            )
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
        last_run_id=job.last_run_id,
        last_run_status=last_status,
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
    agent_id: str = Query(..., min_length=1, max_length=32),
    db: AsyncSession = Depends(get_db),
) -> list[CronJobOut]:
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")
    jobs = await cron_service.list_jobs(db, agent_id)
    run_ids = [job.last_run_id for job in jobs if job.last_run_id]
    status_by_id: dict[str, str] = {}
    if run_ids:
        rows = (
            await db.execute(select(Run.id, Run.status).where(Run.id.in_(run_ids)))
        ).all()
        status_by_id = {str(row[0]): str(row[1]) for row in rows}
    channel_ids = [job.push_channel_id for job in jobs if job.push_channel_id]
    channel_names: dict[str, str] = {}
    if channel_ids:
        rows = (
            await db.execute(select(Channel.id, Channel.name).where(Channel.id.in_(channel_ids)))
        ).all()
        channel_names = {str(row[0]): str(row[1]) for row in rows}
    conversation_ids = [
        job.push_conversation_id for job in jobs if job.push_conversation_id
    ]
    conversation_names: dict[str, str] = {}
    if conversation_ids:
        rows = (
            await db.execute(
                select(ChannelConversation.id, ChannelConversation.display_name).where(
                    ChannelConversation.id.in_(conversation_ids)
                )
            )
        ).all()
        conversation_names = {str(row[0]): str(row[1]) for row in rows}
    return [
        await _job_out(
            db, job, status_by_id, channel_names, conversation_names
        )
        for job in jobs
    ]


@router.post("", response_model=CronJobOut, status_code=201)
async def create_cron_job(
    payload: CronJobCreate, db: AsyncSession = Depends(get_db)
) -> CronJobOut:
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
        )
    except CronNotFound as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    except CronValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CronConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(job)
    return await _job_out(db, job)


@router.put("/{job_id}", response_model=CronJobOut)
async def update_cron_job(
    job_id: str, payload: CronJobUpdate, db: AsyncSession = Depends(get_db)
) -> CronJobOut:
    job = await db.get(CronJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="cron job not found")
    agent = await db.get(Agent, job.agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")
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
        )
    except CronNotFound as exc:
        raise HTTPException(status_code=404, detail="cron job not found") from exc
    except CronValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CronConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(job)
    return await _job_out(db, job)


@router.delete("/{job_id}")
async def delete_cron_job(
    job_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    job = await db.get(CronJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="cron job not found")
    try:
        await cron_service.delete_job(db, job_id, agent_id=job.agent_id)
    except CronNotFound as exc:
        raise HTTPException(status_code=404, detail="cron job not found") from exc
    await db.commit()
    return {"deleted": True}


@router.post("/{job_id}/run", response_model=CronRunOut, status_code=201)
async def run_cron_job_now(
    job_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> CronRunOut:
    job = await db.get(CronJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="cron job not found")
    active = (
        await db.scalar(
            select(Run)
            .where(
                Run.session_id == job.session_id,
                Run.status.in_(ACTIVE_RUN_STATUSES),
            )
            .limit(1)
        )
        if job.session_id
        else None
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "任务会话里已有运行中的 Run，请稍后再试",
                "active_run_id": active.id,
                "status": active.status,
            },
        )
    try:
        run = await run_job_now(job.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="cron job not found") from exc
    except CronInactiveAgent as exc:
        raise HTTPException(status_code=400, detail="Agent 已归档或暂停，无法运行") from exc
    except CronBusy as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "任务会话里已有运行中的 Run，请稍后再试",
                "active_run_id": exc.run_id,
                "status": exc.status,
            },
        ) from exc
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.wake()
    return CronRunOut(run_id=run.id, session_id=run.session_id)
