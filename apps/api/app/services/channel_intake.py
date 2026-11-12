from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import ChannelDelivery, ChannelInbound
from app.services import channel_delivery
from app.services.claude_runner import DeliveryRequest, Turn, transcript_path
from app.services.commands import (
    HELP_TEXT,
    NEW_SESSION_TEXT,
    STOP_EMPTY_TEXT,
    STOP_REQUESTED_TEXT,
    UNKNOWN_COMMAND_TEXT,
    parse_command,
)
from app.services.identifiers import is_canonical_uuid, valid_agent_id
from app.services.settings_store import ConversationSection

logger = logging.getLogger(__name__)

# Quote-replies arrive without text; look the quoted message up by time.
QUOTE_LOOKUP_WINDOW_SECONDS = 30.0
QUOTE_LOOKUP_LATE_SECONDS = 2.0


@dataclass
class IngestResult:
    session_id: str | None = None
    duplicate: bool = False
    reply: str | None = None


def _conversation_key(channel_id: str, external_id: str) -> str:
    """Bindings are scoped per channel; two bots may see the same contact id."""
    return f"{channel_id}:{external_id}"


def _normalize_message_ids(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("external_message_ids must be a list")
    message_ids = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("external_message_ids must be non-empty strings")
        message_ids.append(value.strip())
    if not message_ids:
        raise ValueError("external_message_ids is required")
    # A batch may repeat an id after a partial iLink replay; claim it once.
    return list(dict.fromkeys(message_ids))


async def inbound_seen(channel_id: str, message_ids: list[str]) -> set[str]:
    """Return the ids in this channel that are already claimed."""
    ids = [message_id for message_id in message_ids if message_id]
    if not ids:
        return set()
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(ChannelInbound.external_message_id).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id.in_(ids),
            )
        )
        return set(rows.all())


async def claim_inbound(
    channel_id: str,
    message_ids: list[str],
    text: str = "",
    external_id: str = "",
) -> bool:
    """Claim every message id in one transaction.

    ``False`` means the batch was already claimed (duplicate). A unique
    violation that turns out not to be one of these rows is re-raised. The
    inbound text and contact id are stored on each row so quote-replies can
    recover the quoted message later; a duplicate claim keeps the first copy.
    """
    async with SessionLocal() as db:
        db.add_all(
            [
                ChannelInbound(
                    channel_id=channel_id,
                    external_message_id=message_id,
                    text=text,
                    external_id=external_id,
                )
                for message_id in message_ids
            ]
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(ChannelInbound.id)
                .where(
                    ChannelInbound.channel_id == channel_id,
                    ChannelInbound.external_message_id.in_(message_ids),
                )
                .limit(1)
            )
            if existing is not None:
                return False
            raise
        return True


async def resolve_quoted_text(
    channel_id: str,
    external_id: str,
    msg_id: str | None,
    create_time_ms: int | None,
) -> str | None:
    """Recover the text of a quoted message iLink omitted from the payload.

    Tries the exact message id first, then this contact's inbound rows inside
    the quote time window, and finally this contact's bot replies in that
    window. Returns ``None`` when nothing usable is stored.
    """
    async with SessionLocal() as db:
        if msg_id:
            exact = await db.scalar(
                select(ChannelInbound.text)
                .where(
                    ChannelInbound.channel_id == channel_id,
                    ChannelInbound.external_id == external_id,
                    ChannelInbound.external_message_id == msg_id,
                )
                .limit(1)
            )
            if isinstance(exact, str) and exact.strip():
                return exact

        if create_time_ms is None:
            return None
        try:
            target = datetime.fromtimestamp(
                int(create_time_ms) / 1000, tz=timezone.utc
            )
        except (TypeError, ValueError, OSError, OverflowError):
            return None
        start = target - timedelta(seconds=QUOTE_LOOKUP_LATE_SECONDS)
        end = target + timedelta(seconds=QUOTE_LOOKUP_WINDOW_SECONDS)
        target_text = target.strftime("%Y-%m-%d %H:%M:%S.%f")

        inbound = await db.scalar(
            select(ChannelInbound.text)
            .where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_id == external_id,
                ChannelInbound.created_at >= start,
                ChannelInbound.created_at <= end,
                ChannelInbound.text != "",
            )
            .order_by(
                func.abs(
                    func.julianday(ChannelInbound.created_at)
                    - func.julianday(target_text)
                )
            )
            .limit(1)
        )
        if isinstance(inbound, str) and inbound.strip():
            return inbound

        # A bot reply is stamped when it finished sending, not when the
        # obligation row was created; fall back to created_at only for older
        # rows that were never delivered.
        delivery_time = func.coalesce(
            ChannelDelivery.delivered_at, ChannelDelivery.created_at
        )
        content = await db.scalar(
            select(ChannelDelivery.content)
            .where(
                ChannelDelivery.channel_id == channel_id,
                ChannelDelivery.external_id == external_id,
                delivery_time >= start,
                delivery_time <= end,
                ChannelDelivery.content != "",
            )
            .order_by(
                func.abs(
                    func.julianday(delivery_time) - func.julianday(target_text)
                )
            )
            .limit(1)
        )
        if isinstance(content, str) and content.strip():
            return content
        return None


async def _release_inbound(channel_id: str, message_ids: list[str]) -> None:
    """Undo a claim after a failed ingest so the batch can be retried."""
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelInbound).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id.in_(message_ids),
            )
        )
        await db.commit()


async def _record_inbound(
    channel_id: str, message_ids: list[str], session_id: str | None
) -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(ChannelInbound)
            .where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id.in_(message_ids),
            )
            .values(session_id=session_id)
        )
        await db.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_conversation(
    channel_id: str, display_name: str, context_token: str | None
) -> ConversationSection:
    return ConversationSection(
        session_id=str(uuid.uuid4()),
        channel_id=channel_id,
        display_name=display_name,
        context_token=context_token,
        updated_at=_now_iso(),
    )


def _binding_is_valid(
    conversation: ConversationSection, channel_id: str, agent_id: str, registry: Any
) -> bool:
    """A binding is reusable while its session is queued/running or exists.

    A brand-new session has no transcript until the SDK starts its first turn,
    so the registry's active claim keeps the queued window valid. A deleted
    session leaves the binding behind with neither signal; the next inbound
    message must start a fresh session instead of resurrecting the stale id.
    """
    if conversation.channel_id != channel_id:
        return False
    if not valid_agent_id(agent_id) or not is_canonical_uuid(conversation.session_id):
        return False
    return (
        registry.is_busy(conversation.session_id)
        or transcript_path(conversation.session_id, agent_id).is_file()
    )


async def _command_reply(
    command: str,
    *,
    conversation: ConversationSection,
    agent_id: str,
    registry: Any,
) -> str:
    # /new and /clear are intercepted by ``ingest`` before this helper runs.
    if command == "/help":
        return HELP_TEXT
    if command == "/status":
        busy = registry.is_busy(conversation.session_id)
        return (
            f"Agent：{agent_id}\n会话：{conversation.session_id}\n"
            f"状态：{'执行中' if busy else '空闲'}"
        )
    if command == "/stop":
        stopped = await registry.interrupt(conversation.session_id)
        return STOP_REQUESTED_TEXT if stopped else STOP_EMPTY_TEXT
    return UNKNOWN_COMMAND_TEXT


def _choose_conversation(
    settings: Any,
    *,
    channel_id: str,
    external_id: str,
    display_name: str,
    context_token: str | None,
    agent_id: str,
    command: str | None,
    registry: Any,
) -> tuple[str, str | None, ConversationSection, str | None]:
    """Pick the session binding to write for this inbound message.

    Also returns the session id the decision was based on, so the store can
    compare-and-set and let the caller retry when a concurrent turn rebinds
    the contact first, plus the dead session a stale binding pointed at (if
    any) so its delivery rows can be dropped after the rebind commits.
    """
    key = _conversation_key(channel_id, external_id)
    existing = settings.channel_conversations.get(key)
    stale_session_id: str | None = None
    if existing is not None and existing.channel_id == channel_id:
        token = context_token or existing.context_token
        name = display_name or existing.display_name
        if _binding_is_valid(existing, channel_id, agent_id, registry):
            conversation = existing.model_copy(
                update={
                    "display_name": name,
                    "context_token": token,
                    "updated_at": _now_iso(),
                }
            )
        else:
            stale_session_id = existing.session_id
            conversation = _new_conversation(channel_id, name, token)
    else:
        # Unknown key or a binding owned by another channel: never reuse its
        # session or carry its context token across channels.
        conversation = _new_conversation(channel_id, display_name, context_token)

    if command in ("/new", "/clear"):
        # An explicit rebind keeps the old session (and its deliveries) even
        # when it is no longer the active one.
        conversation = _new_conversation(
            channel_id,
            conversation.display_name,
            context_token or conversation.context_token,
        )
    expected = existing.session_id if existing is not None else None
    return key, expected, conversation, stale_session_id


async def _discard_stale_session_deliveries(
    session_id: str, agent_id: str, registry: Any
) -> None:
    """Drop delivery rows owned by a dead binding after it was replaced.

    Claude deletes expired transcripts itself; the platform still owns these
    rows. A session that is running or whose transcript still exists is never
    touched (for example a /new rebind of a live conversation).
    """
    if not is_canonical_uuid(session_id):
        return
    try:
        if registry is not None and registry.is_busy(session_id):
            return
        if transcript_path(session_id, agent_id).is_file():
            return
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(
                    ChannelDelivery.session_id == session_id
                )
            )
            await db.commit()
    except Exception:
        logger.warning(
            "failed to delete deliveries for stale session=%s",
            session_id,
            exc_info=True,
        )


async def ingest(
    *,
    store: Any,
    registry: Any,
    channel_id: str,
    external_message_ids: list[str],
    external_id: str,
    display_name: str,
    text: str,
    context_token: str | None,
) -> IngestResult:
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    external_id = (external_id or "").strip()
    if not external_id:
        raise ValueError("external_id is required")
    message_ids = _normalize_message_ids(external_message_ids)

    command = parse_command(text)
    if not await claim_inbound(channel_id, message_ids, text, external_id):
        return IngestResult(duplicate=True)

    delivery_id: int | None = None
    submitted = False
    try:
        conversation: ConversationSection | None = None
        agent: Any = None
        # A concurrent inbound turn may rebind the contact between our read and
        # write (notably /new racing an in-flight flush); retry against the
        # latest settings instead of resurrecting the session we read.
        for _attempt in range(3):
            settings = await store.get()
            channel = next(
                (item for item in settings.channels if item.id == channel_id),
                None,
            )
            if channel is None or channel.status != "active":
                raise ValueError("channel is not active")
            agent = next(
                (item for item in settings.agents if item.id == channel.agent_id),
                None,
            )
            if agent is None or agent.status != "active":
                raise ValueError("agent is not available")
            key, expected, candidate, stale_session_id = _choose_conversation(
                settings,
                channel_id=channel_id,
                external_id=external_id,
                display_name=display_name,
                context_token=context_token,
                agent_id=agent.id,
                command=command,
                registry=registry,
            )
            if await store.update_conversation_if_unchanged(
                key, expected, candidate
            ):
                conversation = candidate
                if stale_session_id is not None:
                    await _discard_stale_session_deliveries(
                        stale_session_id, agent.id, registry
                    )
                break
        if conversation is None:
            raise RuntimeError("conversation binding changed concurrently")

        if command is not None:
            if command in ("/new", "/clear"):
                reply = NEW_SESSION_TEXT
            else:
                reply = await _command_reply(
                    command,
                    conversation=conversation,
                    agent_id=agent.id,
                    registry=registry,
                )
            await _record_inbound(channel_id, message_ids, conversation.session_id)
            return IngestResult(session_id=conversation.session_id, reply=reply)

        # Record the reply this message will owe before it enters the queue, so
        # a crash or graceful shutdown while it waits cannot lose it silently
        # (iLink dedups the replay against the inbound claim). The runner
        # promotes the row to pending when the turn actually starts.
        async with SessionLocal() as db:
            delivery_id = await channel_delivery.create_queued(
                db,
                channel_id=channel_id,
                external_id=external_id,
                session_id=conversation.session_id,
            )
            await db.commit()
        await registry.submit(
            Turn(
                session_id=conversation.session_id,
                agent_id=agent.id,
                prompt=text,
                source="wechat",
                delivery=DeliveryRequest(
                    channel_id=channel_id,
                    external_id=external_id,
                    delivery_id=delivery_id,
                ),
            )
        )
        submitted = True
        # The turn is already submitted, so a bookkeeping failure here must not
        # release the claim: a replay would run the turn a second time. The
        # channel runtime gets the session id in the return value regardless.
        try:
            await _record_inbound(channel_id, message_ids, conversation.session_id)
        except Exception:
            logger.warning(
                "failed to record inbound session channel=%s session=%s",
                channel_id,
                conversation.session_id,
                exc_info=True,
            )
        return IngestResult(session_id=conversation.session_id)
    except BaseException:
        if not submitted:
            # The turn never entered the queue, so drop its queued obligation
            # and leave no claimed ids behind: the channel runtime retries the
            # whole batch after a failure, and claimed-but-unprocessed ids
            # would be silently dropped as duplicates.
            if delivery_id is not None:
                try:
                    await channel_delivery.discard(delivery_id)
                except Exception:
                    logger.warning(
                        "failed to discard queued delivery delivery=%s",
                        delivery_id,
                        exc_info=True,
                    )
            try:
                await _release_inbound(channel_id, message_ids)
            except Exception:
                logger.warning(
                    "failed to release inbound claim channel=%s",
                    channel_id,
                    exc_info=True,
                )
        # A submitted turn owns its inbound claim and queued obligation; a
        # cancellation here must not hand them to a replay or drop the row.
        raise
