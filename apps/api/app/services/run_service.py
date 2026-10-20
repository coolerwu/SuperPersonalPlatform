from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Agent, Message, Provider, Run, RunEvent, Session, utcnow

ACTIVE_RUN_STATUSES = ("queued", "running")


class ActiveRunConflict(Exception):
    def __init__(self, run_id: str, status: str) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(f"session already has active run {run_id} ({status})")


async def seed_default_provider() -> None:
    async with SessionLocal() as db:
        existing = await db.scalar(select(Provider).limit(1))
        if existing is not None:
            changed = False
            if not existing.api_key and settings.llm_api_key:
                existing.api_key = settings.llm_api_key
                changed = True
            if not existing.base_url and settings.llm_base_url:
                existing.base_url = settings.llm_base_url
                changed = True
            if not existing.model and settings.llm_model:
                existing.model = settings.llm_model
                changed = True
            if changed:
                await db.commit()
            return
        db.add(
            Provider(
                id="default",
                name="DeepSeek",
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
            )
        )
        await db.commit()


async def _create_run_in_db(
    db: AsyncSession,
    session: Session,
    content: str,
    *,
    source: str = "manual",
) -> tuple[Run, Message]:
    """Create the user Message + queued Run in the caller's transaction."""
    message = Message(session_id=session.id, role="user", content=content)
    db.add(message)
    await db.flush()

    run = Run(
        agent_id=session.agent_id,
        session_id=session.id,
        source=source,
        input=content,
        status="queued",
    )
    db.add(run)
    await db.flush()
    message.run_id = run.id

    if not session.title or session.title == "新会话":
        session.title = content.strip()[:24] or "新会话"
    session.updated_at = utcnow()
    db.add(RunEvent(run_id=run.id, type="run_queued", payload={"content": content}))
    return run, message


async def create_run(
    session_id: str,
    content: str,
    *,
    source: str = "manual",
    allow_queue: bool = False,
) -> tuple[Run, Message]:
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        if session is None:
            raise LookupError("session not found")
        if not allow_queue:
            active = await db.scalar(
                select(Run)
                .where(Run.session_id == session_id, Run.status.in_(ACTIVE_RUN_STATUSES))
                .order_by(Run.created_at.desc())
            )
            if active is not None:
                raise ActiveRunConflict(active.id, active.status)
        agent = await db.get(Agent, session.agent_id)
        if agent is None:
            raise LookupError("agent not found")

        run, message = await _create_run_in_db(db, session, content, source=source)
        await db.commit()
        await db.refresh(run)
        await db.refresh(message)
        return run, message


async def active_run_for_session(db: AsyncSession, session_id: str) -> Run | None:
    """Return the Run the UI should track: the running one, else the oldest queued."""
    return await db.scalar(
        select(Run)
        .where(Run.session_id == session_id, Run.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(case((Run.status == "running", 0), else_=1), Run.created_at)
        .limit(1)
    )


async def append_event(run_id: str, event_type: str, payload: dict[str, Any]) -> RunEvent:
    async with SessionLocal() as db:
        event = RunEvent(run_id=run_id, type=event_type, payload=payload)
        db.add(event)
        await db.commit()
        await db.refresh(event)
        return event


async def complete_run(
    run_id: str,
    *,
    output: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> None:
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None or run.status in ("completed", "failed", "cancelled"):
            return
        run.status = "completed"
        run.output = output
        run.input_tokens = input_tokens
        run.output_tokens = output_tokens
        run.total_tokens = total_tokens
        run.finished_at = utcnow()
        if run.session_id:
            db.add(Message(session_id=run.session_id, role="assistant", content=output, run_id=run.id))
            session = await db.get(Session, run.session_id)
            if session is not None:
                session.updated_at = utcnow()
        from app.services import channel_service

        await channel_service.record_delivery(db, run, output)
        db.add(
            RunEvent(
                run_id=run.id,
                type="run_completed",
                payload={
                    "output": output,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                },
            )
        )
        await db.commit()


async def fail_run(run_id: str, error_type: str, message: str) -> None:
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None or run.status in ("completed", "failed", "cancelled"):
            return
        run.status = "failed"
        run.error = f"{error_type}: {message}"
        run.finished_at = utcnow()
        from app.services import channel_service

        await channel_service.record_delivery(db, run, channel_service.DELIVERY_FAILURE_TEXT)
        db.add(
            RunEvent(
                run_id=run.id,
                type="run_failed",
                payload={"error": {"type": error_type, "message": message}},
            )
        )
        await db.commit()


async def cancel_run_record(run_id: str) -> None:
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None or run.status in ("completed", "failed", "cancelled"):
            return
        run.status = "cancelled"
        run.finished_at = utcnow()
        db.add(RunEvent(run_id=run.id, type="run_cancelled", payload={"message": "run cancelled"}))
        await db.commit()


def _requeue_or_cancel(db: AsyncSession, run: Run) -> None:
    if run.cancel_requested:
        run.status = "cancelled"
        run.finished_at = utcnow()
        db.add(RunEvent(run_id=run.id, type="run_cancelled", payload={"message": "run cancelled"}))
        return
    run.status = "queued"
    run.started_at = None
    db.add(RunEvent(run_id=run.id, type="run_queued", payload={"reason": "service_restart"}))


async def requeue_run(run_id: str) -> None:
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None or run.status != "running":
            return
        _requeue_or_cancel(db, run)
        await db.commit()


async def requeue_running_runs() -> None:
    async with SessionLocal() as db:
        runs = (await db.scalars(select(Run).where(Run.status == "running"))).all()
        for run in runs:
            _requeue_or_cancel(db, run)
        await db.commit()


async def get_run_events(run_id: str, after: int = 0) -> list[RunEvent]:
    async with SessionLocal() as db:
        statement = select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > after).order_by(RunEvent.id)
        return list((await db.scalars(statement)).all())


def serialize_event(event: RunEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "type": event.type,
        "payload": event.payload,
        "created_at": _iso(event.created_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
