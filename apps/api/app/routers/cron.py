"""Cron job management endpoints (single-user cookie auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import Agent, CronJob, Run, Session
from app.schemas import CronJobCreate, CronJobOut, CronJobUpdate, CronRunOut
from app.services.cron_service import (
    CronBusy,
    CronInactiveAgent,
    CronScheduleError,
    next_run,
    normalize_schedule,
    run_job_now,
    session_title,
    validate_timezone,
)
from app.services.run_service import ACTIVE_RUN_STATUSES

router = APIRouter(prefix="/api/cron-jobs", tags=["cron"], dependencies=[Depends(require_auth)])


def _clean_name(raw: str) -> str:
    name = str(raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="任务名称不能为空")
    if len(name) > 120:
        raise HTTPException(status_code=400, detail="任务名称不能超过 120 个字符")
    return name


def _clean_prompt(raw: str) -> str:
    prompt = str(raw or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="任务内容不能为空")
    return prompt


def _clean_schedule(raw: str) -> str:
    try:
        return normalize_schedule(raw)
    except CronScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _clean_timezone(raw: str) -> str:
    try:
        return validate_timezone(raw)
    except CronScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _compute_next(job: CronJob) -> None:
    # Always compute once so an expression with no future occurrence is
    # rejected even while the job is paused.
    upcoming = next_run(job.schedule, job.timezone)
    job.next_run_at = upcoming if job.enabled else None


async def _ensure_unique_name(
    db: AsyncSession, agent_id: str, name: str, exclude_id: str | None = None
) -> None:
    statement = select(CronJob.id).where(
        CronJob.agent_id == agent_id, CronJob.name == name
    )
    if exclude_id is not None:
        statement = statement.where(CronJob.id != exclude_id)
    if await db.scalar(statement) is not None:
        raise HTTPException(status_code=409, detail="同一 Agent 下已存在同名定时任务")


async def _job_out(
    db: AsyncSession, job: CronJob, status_by_id: dict[str, str] | None = None
) -> CronJobOut:
    last_status = None
    if job.last_run_id:
        if status_by_id is not None:
            last_status = status_by_id.get(job.last_run_id)
        else:
            last_status = await db.scalar(
                select(Run.status).where(Run.id == job.last_run_id)
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
    jobs = (
        await db.scalars(
            select(CronJob)
            .where(CronJob.agent_id == agent_id)
            .order_by(CronJob.created_at)
        )
    ).all()
    run_ids = [job.last_run_id for job in jobs if job.last_run_id]
    status_by_id: dict[str, str] = {}
    if run_ids:
        rows = (
            await db.execute(select(Run.id, Run.status).where(Run.id.in_(run_ids)))
        ).all()
        status_by_id = {str(row[0]): str(row[1]) for row in rows}
    return [await _job_out(db, job, status_by_id) for job in jobs]


@router.post("", response_model=CronJobOut, status_code=201)
async def create_cron_job(
    payload: CronJobCreate, db: AsyncSession = Depends(get_db)
) -> CronJobOut:
    agent = await db.get(Agent, payload.agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")

    name = _clean_name(payload.name)
    prompt = _clean_prompt(payload.prompt)
    schedule = _clean_schedule(payload.schedule)
    timezone = _clean_timezone(payload.timezone)
    await _ensure_unique_name(db, agent.id, name)

    session = Session(
        agent_id=agent.id,
        title=session_title(name),
        origin="cron",
    )
    db.add(session)
    await db.flush()

    job = CronJob(
        agent_id=agent.id,
        name=name,
        prompt=prompt,
        schedule=schedule,
        timezone=timezone,
        enabled=payload.enabled,
        session_id=session.id,
    )
    try:
        _compute_next(job)
    except CronScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.add(job)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同一 Agent 下已存在同名定时任务") from exc
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

    data = payload.model_dump(exclude_unset=True)
    schedule_changed = False
    if data.get("name") is not None:
        name = _clean_name(data["name"])
        await _ensure_unique_name(db, job.agent_id, name, exclude_id=job.id)
        if name != job.name:
            job.name = name
            schedule_changed = True
            session = await db.get(Session, job.session_id) if job.session_id else None
            if session is not None:
                session.title = session_title(name)
    if data.get("prompt") is not None:
        job.prompt = _clean_prompt(data["prompt"])
    if data.get("schedule") is not None:
        schedule = _clean_schedule(data["schedule"])
        if schedule != job.schedule:
            job.schedule = schedule
            schedule_changed = True
    if data.get("timezone") is not None:
        timezone = _clean_timezone(data["timezone"])
        if timezone != job.timezone:
            job.timezone = timezone
            schedule_changed = True
    if data.get("enabled") is not None:
        enabled = bool(data["enabled"])
        if enabled != job.enabled:
            job.enabled = enabled
            schedule_changed = True

    if schedule_changed or (job.enabled and job.next_run_at is None):
        try:
            _compute_next(job)
        except CronScheduleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif not job.enabled:
        job.next_run_at = None

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同一 Agent 下已存在同名定时任务") from exc
    await db.refresh(job)
    return await _job_out(db, job)


@router.delete("/{job_id}")
async def delete_cron_job(
    job_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    job = await db.get(CronJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="cron job not found")
    await db.delete(job)
    await db.commit()
    return {"deleted": True}


@router.post("/{job_id}/run", response_model=CronRunOut, status_code=201)
async def run_cron_job_now(
    job_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> CronRunOut:
    job = await db.get(CronJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="cron job not found")
    active = await db.scalar(
        select(Run)
        .where(Run.session_id == job.session_id, Run.status.in_(ACTIVE_RUN_STATUSES))
        .limit(1)
    ) if job.session_id else None
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
