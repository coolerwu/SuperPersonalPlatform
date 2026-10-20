"""Channel domain logic: inbound ingestion, run links, and delivery records.

The WeChat runtime (``weixin_runtime``) owns the network side; this module only
touches the database so it can be unit-tested without iLink.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Agent,
    Channel,
    ChannelConversation,
    ChannelDelivery,
    ChannelInbound,
    ChannelRunLink,
    Session,
    utcnow,
)
from app.services.run_service import _create_run_in_db

DELIVERY_FAILURE_TEXT = "处理这条消息时出错了，请稍后在网页端查看或重试。"
DELIVERY_TRUNCATED_TEXT = "（内容过长，已截断）"
MAX_REPLY_CHUNKS = 10


class ChannelUnavailable(Exception):
    """The channel or its Agent cannot accept inbound messages right now."""


@dataclass(frozen=True)
class InboundResult:
    run_id: str | None
    session_id: str | None
    message_id: str | None
    duplicate: bool = False


def _session_title(conversation_name: str, text: str) -> str:
    label = (conversation_name or text).strip().replace("\n", " ")
    return f"微信 · {label[:24]}" if label else "微信 · 新会话"


def _split_blocks(text: str) -> list[str]:
    """Split on blank lines, but keep fenced code blocks together."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        current.append(line)
        if not in_fence and not line.strip():
            block = "\n".join(current).strip("\n")
            if block.strip():
                blocks.append(block)
            current = []
    block = "\n".join(current).strip("\n")
    if block.strip():
        blocks.append(block)
    return blocks


def split_reply(text: str, max_chars: int | None = None) -> list[str]:
    """Split a final answer into WeChat-sized chunks at logical boundaries."""
    limit = max_chars or settings.channel_reply_max_chars
    if limit <= 0:
        return [text.strip() or "（无回复）"]
    content = (text or "").strip()
    if not content:
        return ["（无回复）"]
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for block in _split_blocks(content):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        flush()
        if len(block) <= limit:
            current = block
            continue
        for line in block.splitlines():
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
                continue
            flush()
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
    flush()

    if len(chunks) > MAX_REPLY_CHUNKS:
        chunks = chunks[:MAX_REPLY_CHUNKS]
        room = max(0, limit - len(DELIVERY_TRUNCATED_TEXT))
        chunks[-1] = chunks[-1][:room] + DELIVERY_TRUNCATED_TEXT
    return chunks or [content[:limit]]


async def record_delivery(db: AsyncSession, run, content: str) -> ChannelDelivery | None:
    """Record the final answer as owed to the channel in the run's transaction."""
    link = await db.get(ChannelRunLink, run.id)
    if link is None:
        return None
    existing = await db.get(ChannelDelivery, run.id)
    if existing is not None:
        return existing
    delivery = ChannelDelivery(
        run_id=run.id,
        channel_id=link.channel_id,
        conversation_id=link.conversation_id,
        content=content or "",
    )
    db.add(delivery)
    await db.flush()
    return delivery


async def ingest_inbound(
    *,
    channel_id: str,
    external_message_id: str,
    external_conversation_id: str,
    conversation_name: str = "",
    sender_name: str = "",
    text: str,
    context_token: str | None = None,
) -> InboundResult:
    """Map one WeChat DM to a Session + queued Run, idempotently."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    if len(text) > settings.channel_inbound_max_chars:
        raise ValueError("text is too long")
    if not external_message_id or not external_conversation_id:
        raise ValueError("message and conversation ids are required")

    async with SessionLocal() as db:
        channel = await db.get(Channel, channel_id)
        if channel is None or channel.type != "wechat":
            raise ChannelUnavailable("channel not found")
        if channel.status != "active":
            raise ChannelUnavailable("channel is not active")
        agent = await db.get(Agent, channel.agent_id)
        if agent is None or agent.status == "archived":
            raise ChannelUnavailable("agent is not available")

        # Reserve the idempotency key first; this also takes SQLite's write
        # lock, so a concurrent duplicate cannot create a second conversation.
        inbound = ChannelInbound(
            channel_id=channel_id,
            external_message_id=external_message_id,
        )
        db.add(inbound)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(ChannelInbound).where(
                    ChannelInbound.channel_id == channel_id,
                    ChannelInbound.external_message_id == external_message_id,
                )
            )
            if existing is None:
                raise
            return InboundResult(
                run_id=existing.run_id,
                session_id=existing.session_id,
                message_id=existing.message_id,
                duplicate=True,
            )

        conversation = await db.scalar(
            select(ChannelConversation).where(
                ChannelConversation.channel_id == channel_id,
                ChannelConversation.external_id == external_conversation_id,
            )
        )
        if conversation is None:
            session = Session(
                agent_id=channel.agent_id,
                title=_session_title(conversation_name or sender_name, text),
            )
            db.add(session)
            await db.flush()
            conversation = ChannelConversation(
                channel_id=channel_id,
                external_id=external_conversation_id,
                display_name=conversation_name or sender_name,
                session_id=session.id,
            )
            db.add(conversation)
            await db.flush()
        else:
            session = await db.get(Session, conversation.session_id)
            if session is None:
                # The session was deleted; keep the dedup row but start fresh.
                session = Session(
                    agent_id=channel.agent_id,
                    title=_session_title(conversation_name or sender_name, text),
                )
                db.add(session)
                await db.flush()
                conversation.session_id = session.id
            if conversation_name and conversation.display_name != conversation_name:
                conversation.display_name = conversation_name

        run, message = await _create_run_in_db(db, session, text, source="wechat")
        db.add(
            ChannelRunLink(
                run_id=run.id,
                channel_id=channel_id,
                conversation_id=conversation.id,
            )
        )
        inbound.conversation_id = conversation.id
        inbound.session_id = session.id
        inbound.message_id = message.id
        inbound.run_id = run.id

        if context_token:
            conversation.context_token = context_token
            conversation.context_token_updated_at = utcnow()
        channel.last_seen_at = utcnow()
        channel.last_error = None
        await db.commit()
        return InboundResult(
            run_id=run.id,
            session_id=session.id,
            message_id=message.id,
            duplicate=False,
        )


async def unlink_session(db: AsyncSession, session_id: str) -> int:
    """Drop a deleted Session's channel binding and pending deliveries.

    Inbound idempotency rows are kept so a delayed gateway retry cannot
    resurrect an old message as a fresh Run.
    """
    conversation_ids = list(
        (
            await db.scalars(
                select(ChannelConversation.id).where(
                    ChannelConversation.session_id == session_id
                )
            )
        ).all()
    )
    if not conversation_ids:
        return 0
    await db.execute(
        delete(ChannelDelivery).where(ChannelDelivery.conversation_id.in_(conversation_ids))
    )
    await db.execute(
        delete(ChannelRunLink).where(ChannelRunLink.conversation_id.in_(conversation_ids))
    )
    await db.execute(
        delete(ChannelConversation).where(ChannelConversation.id.in_(conversation_ids))
    )
    return len(conversation_ids)


async def delete_channels(db: AsyncSession, channel_ids: list[str]) -> int:
    """Delete channels and their channel-only rows, keeping Sessions/Runs."""
    if not channel_ids:
        return 0
    await db.execute(delete(ChannelDelivery).where(ChannelDelivery.channel_id.in_(channel_ids)))
    await db.execute(delete(ChannelRunLink).where(ChannelRunLink.channel_id.in_(channel_ids)))
    await db.execute(delete(ChannelInbound).where(ChannelInbound.channel_id.in_(channel_ids)))
    await db.execute(
        delete(ChannelConversation).where(ChannelConversation.channel_id.in_(channel_ids))
    )
    result = await db.execute(delete(Channel).where(Channel.id.in_(channel_ids)))
    return int(result.rowcount or 0)


_WHITESPACE_RE = re.compile(r"\s+")


def message_preview(text: str, limit: int = 40) -> str:
    """Single-line preview for logs (never includes tokens)."""
    return _WHITESPACE_RE.sub(" ", text).strip()[:limit]
