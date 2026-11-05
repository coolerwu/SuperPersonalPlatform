from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import (
    Agent,
    Channel,
    ChannelConversation,
    Message,
    Session,
)
from app.schemas import (
    MessageOut,
    MessagePageOut,
    MessageSearchHitOut,
    RunOut,
    SessionCreate,
    SessionOut,
)
from app.services import session_service
from app.services.run_service import active_run_for_session
from app.services.session_search import search_messages

router = APIRouter(prefix="/api", tags=["sessions"], dependencies=[Depends(require_auth)])


async def _message_count(db: AsyncSession, session_id: str) -> int:
    return int(await db.scalar(select(func.count(Message.id)).where(Message.session_id == session_id)) or 0)


async def _channel_labels(
    db: AsyncSession, session_id: str
) -> tuple[str | None, str | None]:
    row = (
        await db.execute(
            select(Channel.type, Channel.name)
            .join(ChannelConversation, ChannelConversation.channel_id == Channel.id)
            .where(ChannelConversation.session_id == session_id)
        )
    ).first()
    return (row[0], row[1]) if row is not None else (None, None)


async def _session_out(
    db: AsyncSession,
    session: Session,
    channel: tuple[str | None, str | None] | None = None,
    last_message_at: datetime | None = None,
) -> SessionOut:
    active = await active_run_for_session(db, session.id)
    if channel is None:
        channel = await _channel_labels(db, session.id)
    channel_type, channel_name = channel
    return SessionOut(
        id=session.id,
        agent_id=session.agent_id,
        title=session.title,
        origin=session.origin,
        message_count=await _message_count(db, session.id),
        active_run_id=active.id if active is not None else None,
        active_run_status=active.status if active is not None else None,
        channel_type=channel_type,
        channel_name=channel_name,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_message_at=last_message_at or session.created_at,
    )


@router.get("/agents/{agent_id}/sessions", response_model=list[SessionOut])
async def list_sessions(agent_id: str, db: AsyncSession = Depends(get_db)) -> list[SessionOut]:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    last_message_at = (
        select(func.max(Message.created_at))
        .where(Message.session_id == Session.id)
        .correlate(Session)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(Session, last_message_at.label("last_message_at"))
            .where(Session.agent_id == agent_id)
            .order_by(
                func.coalesce(last_message_at, Session.created_at).desc(),
                Session.id.desc(),
            )
        )
    ).all()
    sessions = [row[0] for row in rows]
    last_by_session = {row[0].id: row[1] for row in rows}
    channel_by_session: dict[str, tuple[str | None, str | None]] = {}
    if sessions:
        rows = (
            await db.execute(
                select(ChannelConversation.session_id, Channel.type, Channel.name)
                .join(Channel, Channel.id == ChannelConversation.channel_id)
                .where(
                    ChannelConversation.session_id.in_([session.id for session in sessions])
                )
            )
        ).all()
        channel_by_session = {
            str(row[0]): (str(row[1]), str(row[2])) for row in rows
        }
    return [
        await _session_out(
            db,
            session,
            channel_by_session.get(session.id, (None, None)),
            last_by_session.get(session.id),
        )
        for session in sessions
    ]


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


@router.get("/sessions/search", response_model=list[MessageSearchHitOut])
async def search_session_messages(
    agent_id: str,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
) -> list[MessageSearchHitOut]:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    hits = await search_messages(agent_id=agent_id, query=q, limit=limit)
    return [
        MessageSearchHitOut(
            message_id=hit.message_id,
            session_id=hit.session_id,
            session_title=hit.session_title,
            role=hit.role,
            snippet=hit.snippet,
            created_at=hit.created_at,
        )
        for hit in hits
    ]


@router.get("/sessions/{session_id}/messages/page", response_model=MessagePageOut)
async def list_message_page(
    session_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> MessagePageOut:
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    statement = select(Message).where(Message.session_id == session_id)
    if before:
        cursor = await db.get(Message, before)
        if cursor is None or cursor.session_id != session_id:
            raise HTTPException(status_code=400, detail="invalid before")
        statement = statement.where(
            or_(
                Message.created_at < cursor.created_at,
                and_(Message.created_at == cursor.created_at, Message.id < cursor.id),
            )
        )
    rows = (
        await db.scalars(
            statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    page = list(rows[:limit])
    page.reverse()
    return MessagePageOut(
        items=[MessageOut.model_validate(message) for message in page],
        has_more=has_more,
        next_before=page[0].id if has_more and page else None,
    )


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


@router.get("/sessions/{session_id}/active-run", response_model=RunOut | None)
async def get_active_run(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> RunOut | None:
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    active = await active_run_for_session(db, session_id)
    return RunOut.model_validate(active) if active is not None else None


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    result = await session_service.delete_session_cascade(db, session_id)
    if result == session_service.SESSION_MISSING:
        raise HTTPException(status_code=404, detail="session not found")
    if result == session_service.SESSION_ACTIVE:
        raise HTTPException(status_code=409, detail="session has an active run")
    return {"deleted": True}
