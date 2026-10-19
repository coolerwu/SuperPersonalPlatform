from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import settings
from app.db import get_db
from app.models import Agent, Message, Run, RunEvent, RunMemory, Session, utcnow
from app.schemas import MessageOut, SessionCreate, SessionOut

router = APIRouter(prefix="/api", tags=["sessions"], dependencies=[Depends(require_auth)])


async def _message_count(db: AsyncSession, session_id: str) -> int:
    return int(await db.scalar(select(func.count(Message.id)).where(Message.session_id == session_id)) or 0)


async def _session_out(db: AsyncSession, session: Session) -> SessionOut:
    return SessionOut(
        id=session.id,
        agent_id=session.agent_id,
        title=session.title,
        message_count=await _message_count(db, session.id),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get("/agents/{agent_id}/sessions", response_model=list[SessionOut])
async def list_sessions(agent_id: str, db: AsyncSession = Depends(get_db)) -> list[SessionOut]:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    sessions = (
        await db.scalars(
            select(Session).where(Session.agent_id == agent_id).order_by(Session.updated_at.desc())
        )
    ).all()
    return [await _session_out(db, session) for session in sessions]


@router.post("/agents/{agent_id}/sessions", response_model=SessionOut, status_code=201)
async def create_session(
    agent_id: str,
    payload: SessionCreate,
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")
    session = Session(agent_id=agent_id, title=(payload.title or "新会话").strip() or "新会话")
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return await _session_out(db, session)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: str, db: AsyncSession = Depends(get_db)) -> list[MessageOut]:
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = (
        await db.scalars(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at, Message.id)
        )
    ).all()
    return [MessageOut.model_validate(message) for message in messages]


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    active = await db.scalar(
        select(Run).where(Run.session_id == session_id, Run.status.in_(("queued", "running"))).limit(1)
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="session has an active run")
    run_ids = list((await db.scalars(select(Run.id).where(Run.session_id == session_id))).all())
    if run_ids:
        await db.execute(delete(RunMemory).where(RunMemory.run_id.in_(run_ids)))
        await db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
        await db.execute(delete(Run).where(Run.id.in_(run_ids)))
    await db.execute(delete(Message).where(Message.session_id == session_id))
    await db.commit()
    await db.delete(session)
    await db.commit()
    checkpoint: Path = settings.checkpoint_dir / f"{session_id}.sqlite"
    if checkpoint.exists():
        checkpoint.unlink(missing_ok=True)
    return {"deleted": True}
