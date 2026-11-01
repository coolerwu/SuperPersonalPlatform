from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select, text

from app.db import SessionLocal
from app.models import Message, Session

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 20
MAX_READ_MESSAGES = 21
MAX_BEFORE_AFTER = 10
MAX_FTS_TERMS = 8
MIN_FTS_TOKEN_LENGTH = 3
MAX_SNIPPET_CHARS = 320

TOKEN_SPLIT_RE = re.compile(r"[\s,，。！？!?;；:：/\\|()（）\[\]【】]+")


@dataclass(frozen=True)
class SearchHit:
    message_id: str
    session_id: str
    session_title: str
    role: str
    snippet: str
    created_at: datetime


@dataclass(frozen=True)
class ContextMessage:
    message_id: str
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    session_title: str
    messages: tuple[ContextMessage, ...]
    target_message_id: str = ""


def _tokens(query: str) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in TOKEN_SPLIT_RE.split(query.strip()):
        token = raw.strip()
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= MAX_FTS_TERMS:
            break
    return tokens


def _fts_match(tokens: list[str]) -> str:
    parts = [
        '"' + token.replace('"', '""') + '"'
        for token in tokens
        if len(token) >= MIN_FTS_TOKEN_LENGTH
    ]
    return " OR ".join(parts)


def _like_pattern(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(content: str, tokens: list[str]) -> str:
    value = re.sub(r"\s+", " ", content or "").strip()
    if not value:
        return "（空消息）"
    lowered = value.lower()
    position = -1
    for token in tokens:
        index = lowered.find(token.lower())
        if index >= 0 and (position < 0 or index < position):
            position = index
    if position < 0:
        snippet = value[:MAX_SNIPPET_CHARS]
        if len(value) > MAX_SNIPPET_CHARS:
            snippet += "…"
        return snippet
    start = max(0, position - 80)
    end = min(len(value), position + 220)
    snippet = value[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(value):
        snippet += "…"
    return snippet


def _hit(message: Message, session: Session | None, tokens: list[str]) -> SearchHit:
    return SearchHit(
        message_id=message.id,
        session_id=message.session_id,
        session_title=(session.title if session is not None else "") or "新会话",
        role=message.role,
        snippet=_snippet(message.content, tokens),
        created_at=message.created_at,
    )


async def _load_sessions(db, messages: list[Message]) -> dict[str, Session]:  # noqa: ANN001
    session_ids = {message.session_id for message in messages}
    if not session_ids:
        return {}
    rows = (await db.scalars(select(Session).where(Session.id.in_(session_ids)))).all()
    return {session.id: session for session in rows}


async def _search_fts(
    *, agent_id: str, match: str, tokens: list[str], limit: int
) -> list[SearchHit]:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT m.id
                    FROM messages_fts
                    JOIN messages m ON m.rowid = messages_fts.rowid
                    JOIN sessions s ON s.id = m.session_id
                    WHERE messages_fts MATCH :match AND s.agent_id = :agent_id
                    ORDER BY bm25(messages_fts)
                    LIMIT :limit
                    """
                ),
                {"match": match, "agent_id": agent_id, "limit": limit},
            )
        ).all()
        ids = [str(row[0]) for row in rows]
        if not ids:
            return []
        found = {
            message.id: message
            for message in (
                await db.scalars(select(Message).where(Message.id.in_(ids)))
            ).all()
        }
        messages = [found[message_id] for message_id in ids if message_id in found]
        sessions = await _load_sessions(db, messages)
        return [_hit(message, sessions.get(message.session_id), tokens) for message in messages]


async def _search_like(
    *, agent_id: str, tokens: list[str], limit: int
) -> list[SearchHit]:
    patterns = [_like_pattern(token) for token in tokens]
    if not patterns:
        return []
    statement = (
        select(Message)
        .join(Session, Session.id == Message.session_id)
        .where(Session.agent_id == agent_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    for pattern in patterns:
        statement = statement.where(Message.content.like(pattern, escape="\\"))
    async with SessionLocal() as db:
        messages = list((await db.scalars(statement)).all())
        sessions = await _load_sessions(db, messages)
        return [_hit(message, sessions.get(message.session_id), tokens) for message in messages]


async def search_messages(
    *, agent_id: str, query: str, limit: int = DEFAULT_SEARCH_LIMIT
) -> list[SearchHit]:
    """Search the current agent's historical messages by keyword."""
    agent_id = str(agent_id or "").strip()
    query = str(query or "").strip()
    if not agent_id or not query:
        return []
    limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    tokens = _tokens(query)
    if not tokens:
        tokens = [query[:64]]
    match = _fts_match(tokens)
    if match:
        try:
            hits = await _search_fts(
                agent_id=agent_id, match=match, tokens=tokens, limit=limit
            )
            if hits:
                return hits
        except Exception:
            logger.exception("message FTS query failed; falling back to LIKE")
    try:
        return await _search_like(agent_id=agent_id, tokens=tokens[:4], limit=limit)
    except Exception:
        logger.exception("message LIKE search failed")
        return []


async def read_session_context(
    *,
    agent_id: str,
    session_id: str,
    message_id: str = "",
    before: int = 5,
    after: int = 5,
    limit: int = 10,
) -> SessionContext | None:
    """Read messages around one hit, or the latest messages of a session."""
    agent_id = str(agent_id or "").strip()
    session_id = str(session_id or "").strip()
    message_id = str(message_id or "").strip()
    before = max(0, min(int(before), MAX_BEFORE_AFTER))
    after = max(0, min(int(after), MAX_BEFORE_AFTER))
    limit = max(1, min(int(limit), MAX_READ_MESSAGES))
    if not agent_id or not session_id:
        return None

    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        if session is None or session.agent_id != agent_id:
            return None

        if message_id:
            target = await db.scalar(
                select(Message).where(
                    Message.id == message_id,
                    Message.session_id == session_id,
                )
            )
            if target is None:
                return None
            before_rows = list(
                (
                    await db.scalars(
                        select(Message)
                        .where(Message.session_id == session_id)
                        .where(
                            or_(
                                Message.created_at < target.created_at,
                                and_(
                                    Message.created_at == target.created_at,
                                    Message.id < target.id,
                                ),
                            )
                        )
                        .order_by(Message.created_at.desc(), Message.id.desc())
                        .limit(before)
                    )
                ).all()
            )
            before_rows.reverse()
            after_rows = list(
                (
                    await db.scalars(
                        select(Message)
                        .where(Message.session_id == session_id)
                        .where(
                            or_(
                                Message.created_at > target.created_at,
                                and_(
                                    Message.created_at == target.created_at,
                                    Message.id > target.id,
                                ),
                            )
                        )
                        .order_by(Message.created_at, Message.id)
                        .limit(after)
                    )
                ).all()
            )
            messages = [*before_rows, target, *after_rows]
            target_id = target.id
        else:
            messages = list(
                (
                    await db.scalars(
                        select(Message)
                        .where(Message.session_id == session_id)
                        .order_by(Message.created_at.desc(), Message.id.desc())
                        .limit(limit)
                    )
                ).all()
            )
            messages.reverse()
            target_id = ""

        return SessionContext(
            session_id=session.id,
            session_title=session.title or "新会话",
            messages=tuple(
                ContextMessage(
                    message_id=message.id,
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at,
                )
                for message in messages
            ),
            target_message_id=target_id,
        )
