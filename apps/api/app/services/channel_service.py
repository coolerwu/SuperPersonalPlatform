"""Channel domain logic: inbound ingestion, run links, and delivery records.

The WeChat runtime (``weixin_runtime``) owns the network side; this module only
touches the database so it can be unit-tested without iLink.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, update
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
    CronJob,
    Message,
    Run,
    Session,
    utcnow,
)
from app.services.chat_commands import (
    HELP_TEXT,
    KNOWN_COMMANDS,
    MEMORY_COMMAND_LIMIT,
    MEMORY_COMMAND_SAVED,
    MEMORY_COMMAND_USAGE,
    STOP_EMPTY_TEXT,
    STOP_FINISHED_TEXT,
    STOP_UNAVAILABLE_TEXT,
    UNKNOWN_COMMAND_TEXT,
    is_command_text,
    memory_command_content,
    parse_command,
)
from app.services import memory_service
from app.services.run_service import _create_run_in_db

DELIVERY_FAILURE_TEXT = "处理这条消息时出错了，请稍后在网页端查看或重试。"
DELIVERY_TRUNCATED_TEXT = "（内容过长，已截断）"
MAX_REPLY_CHUNKS = 10


# Quote-replies arrive without text; look the quoted message up by time.
QUOTE_LOOKUP_WINDOW_SECONDS = 30.0
QUOTE_LOOKUP_LATE_SECONDS = 2.0


class ChannelUnavailable(Exception):
    """The channel or its Agent cannot accept inbound messages right now."""


@dataclass(frozen=True)
class InboundResult:
    run_id: str | None
    session_id: str | None
    message_id: str | None
    duplicate: bool = False
    command: str | None = None
    reply: str | None = None
    cancel_run_ids: tuple[str, ...] = ()
    conversation_id: str | None = None


def _session_title(conversation_name: str, text: str) -> str:
    label = (conversation_name or text).strip().replace("\n", " ")
    return f"微信 · {label[:24]}" if label else "微信 · 新会话"


async def _active_run_rows(db: AsyncSession, session_id: str) -> list[tuple[str, str]]:
    rows = await db.execute(
        select(Run.id, Run.status)
        .where(Run.session_id == session_id, Run.status.in_(("queued", "running")))
        .order_by(Run.created_at)
    )
    return [(run_id, status) for run_id, status in rows.all()]


async def _apply_command(
    db: AsyncSession,
    *,
    command: str,
    raw_text: str,
    channel: Channel,
    agent: Agent,
    conversation: ChannelConversation | None,
    external_id: str,
    contact_name: str,
) -> tuple[ChannelConversation | None, Session | None, str | None, tuple[str, ...]]:
    """Run one channel command; returns (conversation, session, reply, runs to cancel)."""
    session = None
    if conversation is not None:
        session = await db.get(Session, conversation.session_id)

    if command not in KNOWN_COMMANDS:
        return conversation, session, UNKNOWN_COMMAND_TEXT, ()

    if command == "/memory":
        content = memory_command_content(raw_text) or ""
        if not content:
            return conversation, session, MEMORY_COMMAND_USAGE, ()
        item = await memory_service.create_memory_in_session(
            db,
            scope="user",
            content=content,
            source_kind="command",
            snapshot_text=raw_text.strip(),
            snapshot_at=utcnow(),
        )
        if item is None:
            return conversation, session, MEMORY_COMMAND_LIMIT, ()
        return (
            conversation,
            session,
            MEMORY_COMMAND_SAVED.format(content=item.content),
            (),
        )

    if command in ("/new", "/clear"):
        active: list[tuple[str, str]] = []
        if session is not None:
            active = await _active_run_rows(db, session.id)
        new_session = Session(
            agent_id=channel.agent_id, title=_session_title(contact_name, "")
        )
        db.add(new_session)
        await db.flush()
        if conversation is None:
            conversation = ChannelConversation(
                channel_id=channel.id,
                external_id=external_id,
                display_name=contact_name,
                session_id=new_session.id,
            )
            db.add(conversation)
            await db.flush()
        else:
            conversation.session_id = new_session.id
        reply = "已开始新会话，之前的对话不会带入；旧会话仍保留在网页端。"
        if active:
            reply += (
                f"\n之前还有 {len(active)} 个任务排队或运行中，"
                "完成后仍会回复到这里。"
            )
        return conversation, new_session, reply, ()

    if command == "/help":
        return conversation, session, HELP_TEXT, ()

    if command == "/status":
        if session is None:
            return conversation, None, "当前还没有会话，直接发消息即可开始新对话。", ()
        active = await _active_run_rows(db, session.id)
        running = sum(1 for _, status in active if status == "running")
        queued = sum(1 for _, status in active if status == "queued")
        reply = (
            f"Agent：{agent.name}\n"
            f"会话：{session.title}\n"
            f"运行中：{running} 个，排队：{queued} 个"
        )
        return conversation, session, reply, ()

    # /stop
    if session is None:
        return conversation, None, STOP_EMPTY_TEXT, ()
    active = await _active_run_rows(db, session.id)
    if not active:
        return conversation, session, STOP_EMPTY_TEXT, ()
    return conversation, session, None, tuple(run_id for run_id, _ in active)


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


async def inbound_seen(channel_id: str, external_message_id: str) -> bool:
    """True when iLink has already delivered (and we recorded) this message id."""
    if not external_message_id:
        return False
    async with SessionLocal() as db:
        existing = await db.scalar(
            select(ChannelInbound.id).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id == external_message_id,
            )
        )
        return existing is not None


async def record_inbound_event(
    *,
    channel_id: str,
    external_message_id: str,
    conversation_id: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Record a message that intentionally creates no Run (e.g. unsupported media)."""
    async with SessionLocal() as db:
        db.add(
            ChannelInbound(
                channel_id=channel_id,
                external_message_id=external_message_id,
                conversation_id=conversation_id,
                session_id=session_id,
            )
        )
        try:
            await db.commit()
            return True
        except IntegrityError:
            await db.rollback()
            return False


async def record_inbound_ids(
    *,
    channel_id: str,
    ids: list[str],
    run_id: str,
    message_id: str,
    conversation_id: str,
    session_id: str,
) -> None:
    """Record every id in a batched packet so replays cannot create a second Run."""
    if not ids:
        return
    async with SessionLocal() as db:
        existing = set(
            (
                await db.scalars(
                    select(ChannelInbound.external_message_id).where(
                        ChannelInbound.channel_id == channel_id,
                        ChannelInbound.external_message_id.in_(ids),
                    )
                )
            ).all()
        )
        changed = False
        for external_id in dict.fromkeys(ids):
            if external_id in existing:
                continue
            db.add(
                ChannelInbound(
                    channel_id=channel_id,
                    external_message_id=external_id,
                    conversation_id=conversation_id,
                    session_id=session_id,
                    message_id=message_id,
                    run_id=run_id,
                )
            )
            changed = True
        if changed:
            await db.commit()


async def resolve_quoted_text(
    *,
    channel_id: str,
    external_conversation_id: str,
    msg_id: str = "",
    create_time_ms: int | None = None,
) -> str:
    """Recover the text of a quote-reply that iLink delivered empty.

    iLink usually strips ``text_item`` from ``ref_msg`` and keeps only
    ``msg_id`` / ``create_time_ms``, so the quoted message is looked up among
    this conversation's own inbound messages and delivered replies.
    """
    async with SessionLocal() as db:
        conversation = await db.scalar(
            select(ChannelConversation).where(
                ChannelConversation.channel_id == channel_id,
                ChannelConversation.external_id == external_conversation_id,
            )
        )
        if conversation is None:
            return ""
        if msg_id:
            message_id = await db.scalar(
                select(ChannelInbound.message_id)
                .where(
                    ChannelInbound.channel_id == channel_id,
                    ChannelInbound.external_message_id == msg_id,
                    ChannelInbound.message_id.is_not(None),
                )
                .order_by(ChannelInbound.id.desc())
                .limit(1)
            )
            if message_id:
                content = await db.scalar(
                    select(Message.content).where(Message.id == message_id)
                )
                if content and content.strip():
                    return content.strip()
        if create_time_ms is None:
            return ""

        target = datetime.fromtimestamp(create_time_ms / 1000, tz=timezone.utc)
        earliest = target - timedelta(seconds=QUOTE_LOOKUP_LATE_SECONDS)
        latest = target + timedelta(seconds=QUOTE_LOOKUP_WINDOW_SECONDS)
        candidates: list[tuple[float, str]] = []

        inbound_rows = await db.execute(
            select(Message.content, ChannelInbound.created_at)
            .join(Message, Message.id == ChannelInbound.message_id)
            .where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.conversation_id == conversation.id,
                ChannelInbound.message_id.is_not(None),
                ChannelInbound.created_at >= earliest,
                ChannelInbound.created_at <= latest,
            )
        )
        for content, recorded_at in inbound_rows.all():
            if content and content.strip():
                candidates.append(
                    (abs((recorded_at - target).total_seconds()), content.strip())
                )

        delivery_rows = await db.execute(
            select(
                ChannelDelivery.content,
                ChannelDelivery.created_at,
                ChannelDelivery.delivered_at,
            ).where(
                ChannelDelivery.channel_id == channel_id,
                ChannelDelivery.conversation_id == conversation.id,
                or_(
                    ChannelDelivery.created_at.between(earliest, latest),
                    ChannelDelivery.delivered_at.between(earliest, latest),
                ),
            )
        )
        for content, created_at, delivered_at in delivery_rows.all():
            sent_at = delivered_at or created_at
            if not (content and content.strip()):
                continue
            if not earliest <= sent_at <= latest:
                continue
            candidates.append(
                (abs((sent_at - target).total_seconds()), content.strip())
            )

        if not candidates:
            return ""
        return min(candidates, key=lambda item: item[0])[1]


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

    command = parse_command(text)

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
                conversation_id=existing.conversation_id,
            )

        conversation = await db.scalar(
            select(ChannelConversation).where(
                ChannelConversation.channel_id == channel_id,
                ChannelConversation.external_id == external_conversation_id,
            )
        )
        contact_name = (conversation_name or sender_name).strip()
        if conversation is not None:
            if contact_name and conversation.display_name != contact_name:
                conversation.display_name = contact_name

        if command is not None:
            conversation, session, reply, cancel_run_ids = await _apply_command(
                db,
                command=command,
                raw_text=text,
                channel=channel,
                agent=agent,
                conversation=conversation,
                external_id=external_conversation_id,
                contact_name=contact_name,
            )
            inbound.conversation_id = conversation.id if conversation is not None else None
            inbound.session_id = session.id if session is not None else None
            if context_token and conversation is not None:
                conversation.context_token = context_token
                conversation.context_token_updated_at = utcnow()
            channel.last_seen_at = utcnow()
            channel.last_error = None
            await db.commit()
            return InboundResult(
                run_id=None,
                session_id=session.id if session is not None else None,
                message_id=None,
                duplicate=False,
                command=command,
                reply=reply,
                cancel_run_ids=cancel_run_ids,
                conversation_id=conversation.id if conversation is not None else None,
            )

        if conversation is None:
            session = Session(
                agent_id=channel.agent_id,
                title=_session_title(contact_name, text),
            )
            db.add(session)
            await db.flush()
            conversation = ChannelConversation(
                channel_id=channel_id,
                external_id=external_conversation_id,
                display_name=contact_name,
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
                    title=_session_title(contact_name, text),
                )
                db.add(session)
                await db.flush()
                conversation.session_id = session.id

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
            conversation_id=conversation.id,
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
        update(CronJob)
        .where(CronJob.push_conversation_id.in_(conversation_ids))
        .values(push_channel_id=None, push_conversation_id=None)
    )
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
    await db.execute(
        update(CronJob)
        .where(CronJob.push_channel_id.in_(channel_ids))
        .values(push_channel_id=None, push_conversation_id=None)
    )
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
