from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import ChannelInbound
from app.services.claude_runner import DeliveryRequest, Turn
from app.services.commands import (
    HELP_TEXT,
    NEW_SESSION_TEXT,
    STOP_EMPTY_TEXT,
    STOP_REQUESTED_TEXT,
    UNKNOWN_COMMAND_TEXT,
    parse_command,
)
from app.services.settings_store import ConversationSection

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    session_id: str | None = None
    duplicate: bool = False
    reply: str | None = None


async def _claim_inbound(channel_id: str, external_message_id: str) -> bool:
    """Insert the idempotency row; False when it already exists."""
    async with SessionLocal() as db:
        db.add(ChannelInbound(channel_id=channel_id, external_message_id=external_message_id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return False
        return True


async def _record_inbound(
    channel_id: str, external_message_id: str, session_id: str | None
) -> None:
    async with SessionLocal() as db:
        row = await db.scalar(
            select(ChannelInbound).where(
                ChannelInbound.channel_id == channel_id,
                ChannelInbound.external_message_id == external_message_id,
            )
        )
        if row is not None:
            row.session_id = session_id
            await db.commit()


def _new_conversation(
    channel_id: str, display_name: str, context_token: str | None
) -> ConversationSection:
    return ConversationSection(
        session_id=str(uuid.uuid4()),
        channel_id=channel_id,
        display_name=display_name,
        context_token=context_token,
    )


async def _command_reply(
    command: str,
    *,
    conversation: ConversationSection,
    agent_id: str,
    registry: Any,
) -> str:
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


async def ingest(
    *,
    store: Any,
    registry: Any,
    channel_id: str,
    external_message_id: str,
    external_id: str,
    display_name: str,
    text: str,
    context_token: str | None,
) -> IngestResult:
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    if not await _claim_inbound(channel_id, external_message_id):
        return IngestResult(duplicate=True)

    settings = await store.get()
    channel = next((item for item in settings.channels if item.id == channel_id), None)
    if channel is None or channel.status != "active":
        raise ValueError("channel is not active")
    agent = next((item for item in settings.agents if item.id == channel.agent_id), None)
    if agent is None or agent.status != "active":
        raise ValueError("agent is not available")

    conversation = settings.channel_conversations.get(external_id)
    if conversation is None:
        conversation = _new_conversation(channel_id, display_name, context_token)
    else:
        conversation.display_name = display_name or conversation.display_name
        if context_token:
            conversation.context_token = context_token
    await store.update_conversation(external_id, conversation)

    command = parse_command(text)
    if command is not None:
        if command in ("/new", "/clear"):
            conversation = _new_conversation(
                channel_id, conversation.display_name, context_token
            )
            await store.update_conversation(external_id, conversation)
            reply = NEW_SESSION_TEXT
        else:
            reply = await _command_reply(
                command,
                conversation=conversation,
                agent_id=agent.id,
                registry=registry,
            )
        await _record_inbound(channel_id, external_message_id, conversation.session_id)
        return IngestResult(session_id=conversation.session_id, reply=reply)

    await registry.submit(
        Turn(
            session_id=conversation.session_id,
            agent_id=agent.id,
            prompt=text,
            source="wechat",
            delivery=DeliveryRequest(channel_id=channel_id, external_id=external_id),
        )
    )
    await _record_inbound(channel_id, external_message_id, conversation.session_id)
    return IngestResult(session_id=conversation.session_id)
