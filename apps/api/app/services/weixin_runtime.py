"""In-process WeChat (iLink) channel runtime.

One poll task per active channel receives DMs; the RunWorker calls
``deliver_run`` right after a channel Run reaches a terminal state. There is no
send loop: pending deliveries are swept only on startup and when a channel
becomes active again.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Channel, ChannelConversation, ChannelDelivery, utcnow
from app.services import channel_service
from app.services.weixin_client import (
    WeixinClient,
    WeixinRateLimited,
    WeixinSessionExpired,
    extract_text,
    is_group_message,
    is_self_message,
)

logger = logging.getLogger(__name__)

RECOVERED_PREFIX = "♻️ 上一条回复可能已部分送达，以下为完整内容：\n\n"
MAX_CONSECUTIVE_FAILURES = 3
RETRY_DELAY_SECONDS = 2.0
BACKOFF_DELAY_SECONDS = 30.0
CHUNK_DELAY_SECONDS = 0.3
MAX_ERROR_TEXT = 500
CONTENT_DEDUP_SECONDS = 300.0


@dataclass(frozen=True)
class ChannelCredentials:
    channel_id: str
    account_id: str
    token: str
    base_url: str
    sync_buf: str


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_TEXT]


class WeixinRuntime:
    def __init__(
        self,
        *,
        worker: Any = None,
        client_factory: Callable[..., WeixinClient] | None = None,
    ) -> None:
        self.worker = worker
        self._client_factory = client_factory or WeixinClient
        self._poll_tasks: dict[str, asyncio.Task[Any]] = {}
        self._clients: dict[str, WeixinClient] = {}
        self._login_sessions: dict[str, dict[str, str]] = {}
        self._delivering: set[str] = set()
        self._recent_text: dict[str, float] = {}
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._stop.clear()
        async with SessionLocal() as db:
            channel_ids = list(
                (
                    await db.scalars(
                        select(Channel.id).where(
                            Channel.type == "wechat", Channel.status == "active"
                        )
                    )
                ).all()
            )
        for channel_id in channel_ids:
            await self.start_channel(channel_id)
        await self.sweep_deliveries()

    async def stop(self) -> None:
        self._stop.set()
        tasks = list(self._poll_tasks.values())
        self._poll_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            try:
                await client.aclose()
            except Exception:
                logger.exception("failed to close weixin client")

    async def start_channel(self, channel_id: str) -> bool:
        if channel_id in self._poll_tasks:
            return True
        credentials = await self._load_credentials(channel_id)
        if credentials is None:
            return False
        client = self._build_client(credentials)
        self._clients[channel_id] = client
        task = asyncio.create_task(
            self._poll_loop(credentials, client),
            name=f"weixin-poll-{channel_id}",
        )
        self._poll_tasks[channel_id] = task
        logger.info("weixin poll started channel=%s account=%s", channel_id, credentials.account_id[:8])
        return True

    async def stop_channel(self, channel_id: str) -> None:
        task = self._poll_tasks.pop(channel_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        client = self._clients.pop(channel_id, None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.exception("failed to close weixin client channel=%s", channel_id)

    def running_channels(self) -> list[str]:
        return [channel_id for channel_id in self._poll_tasks]

    # ------------------------------------------------------------------ polling

    async def _poll_loop(self, credentials: ChannelCredentials, client: WeixinClient) -> None:
        channel_id = credentials.channel_id
        sync_buf = credentials.sync_buf
        failures = 0
        try:
            while not self._stop.is_set():
                try:
                    data = await client.get_updates(sync_buf)
                except asyncio.CancelledError:
                    raise
                except WeixinSessionExpired as exc:
                    logger.warning("weixin login expired channel=%s: %s", channel_id, exc)
                    await self._mark_expired(channel_id, _error_text(exc))
                    return
                except WeixinRateLimited as exc:
                    failures += 1
                    logger.warning("weixin rate limited channel=%s: %s", channel_id, exc)
                    await asyncio.sleep(
                        BACKOFF_DELAY_SECONDS if failures >= MAX_CONSECUTIVE_FAILURES else RETRY_DELAY_SECONDS
                    )
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        failures = 0
                    continue
                except Exception as exc:
                    failures += 1
                    logger.warning(
                        "weixin poll error channel=%s (%d/%d): %s",
                        channel_id,
                        failures,
                        MAX_CONSECUTIVE_FAILURES,
                        exc,
                    )
                    await asyncio.sleep(
                        BACKOFF_DELAY_SECONDS if failures >= MAX_CONSECUTIVE_FAILURES else RETRY_DELAY_SECONDS
                    )
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        failures = 0
                        client = await self._recycle_client(channel_id, client)
                    continue

                failures = 0
                messages = data.get("msgs") or []
                if isinstance(messages, list):
                    for message in messages:
                        try:
                            await self._handle_message(credentials, message)
                        except Exception:
                            logger.exception(
                                "weixin inbound failed channel=%s", channel_id
                            )
                new_buf = str(data.get("get_updates_buf") or "")
                if new_buf and new_buf != sync_buf:
                    sync_buf = new_buf
                    await self._save_sync_buf(channel_id, sync_buf)
                await self._touch_last_seen(channel_id)
        except asyncio.CancelledError:
            raise
        finally:
            current = asyncio.current_task()
            if self._poll_tasks.get(channel_id) is current:
                self._poll_tasks.pop(channel_id, None)
            if self._clients.get(channel_id) is client:
                self._clients.pop(channel_id, None)
            try:
                await client.aclose()
            except Exception:
                logger.exception("failed to close weixin poll client channel=%s", channel_id)

    def _text_seen(self, key: str) -> bool:
        now = time.monotonic()
        if len(self._recent_text) > 512:
            self._recent_text = {
                item: seen
                for item, seen in self._recent_text.items()
                if now - seen < CONTENT_DEDUP_SECONDS
            }
        previous = self._recent_text.get(key)
        return previous is not None and now - previous < CONTENT_DEDUP_SECONDS

    def _remember_text(self, key: str) -> None:
        self._recent_text[key] = time.monotonic()

    async def _handle_message(self, credentials: ChannelCredentials, message: Any) -> None:
        if not isinstance(message, dict):
            return
        sender_id = str(message.get("from_user_id") or "").strip()
        if not sender_id or is_self_message(message, credentials.account_id):
            return
        if is_group_message(message):
            # v1 is DM-only; iLink bot identities usually do not receive group
            # events anyway.
            return
        text = extract_text(message.get("item_list")).strip()
        if not text:
            return
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        dedup_key = f"{credentials.channel_id}:{sender_id}:{digest}"
        if self._text_seen(dedup_key):
            logger.debug("weixin content dedup channel=%s sender=%s", credentials.channel_id, sender_id[:8])
            return
        message_id = str(message.get("message_id") or "").strip()
        if not message_id:
            # iLink normally supplies message_id; the fallback must include a
            # time bucket so identical text is not suppressed forever.
            bucket = message.get("create_time") or message.get("timestamp") or int(time.time() // 60)
            message_id = f"{sender_id}:{bucket}:{digest}"
        sender_name = str(
            message.get("from_user_name")
            or message.get("sender_name")
            or message.get("from_nickname")
            or ""
        ).strip()
        context_token = str(message.get("context_token") or "").strip()
        result = await channel_service.ingest_inbound(
            channel_id=credentials.channel_id,
            external_message_id=message_id,
            external_conversation_id=sender_id,
            conversation_name=sender_name or sender_id,
            sender_name=sender_name,
            text=text,
            context_token=context_token or None,
        )
        if not result.duplicate and self.worker is not None:
            self.worker.wake()
        self._remember_text(dedup_key)

    async def _recycle_client(
        self, channel_id: str, client: WeixinClient
    ) -> WeixinClient:
        if self._clients.get(channel_id) is not client:
            return client
        credentials = await self._load_credentials(channel_id)
        if credentials is None:
            return client
        new_client = self._build_client(credentials)
        self._clients[channel_id] = new_client
        try:
            await client.aclose()
        except Exception:
            logger.exception("failed to close recycled weixin client channel=%s", channel_id)
        return new_client

    # ------------------------------------------------------------------ delivery

    async def deliver_run(self, run_id: str, *, recovered: bool = False) -> None:
        if run_id in self._delivering:
            return
        self._delivering.add(run_id)
        try:
            await self._deliver_run_locked(run_id, recovered=recovered)
        finally:
            self._delivering.discard(run_id)

    async def _deliver_run_locked(self, run_id: str, *, recovered: bool) -> None:
        async with SessionLocal() as db:
            delivery = await db.get(ChannelDelivery, run_id)
            if delivery is None or delivery.status in ("delivered", "failed"):
                return
            if delivery.status == "attempting" and not recovered:
                return
            channel = await db.get(Channel, delivery.channel_id)
            conversation = await db.get(ChannelConversation, delivery.conversation_id)
            if (
                channel is None
                or channel.type != "wechat"
                or channel.status != "active"
                or conversation is None
            ):
                # Leave the obligation pending; a later activation sweeps it.
                return
            content = delivery.content or ""
            external_id = conversation.external_id
            context_token = conversation.context_token
            channel_id = channel.id
            delivery.status = "attempting"
            delivery.attempts = int(delivery.attempts or 0) + 1
            delivery.last_error = None
            await db.commit()

        client = self._clients.get(channel_id)
        temporary = client is None
        if client is None:
            credentials = await self._load_credentials(channel_id)
            if credentials is None:
                await self._set_delivery_pending(run_id, "channel credentials are missing", failed=False)
                return
            client = self._build_client(credentials)

        try:
            chunks = channel_service.split_reply(content)
            if recovered and chunks:
                chunks[0] = f"{RECOVERED_PREFIX}{chunks[0]}"
            for index, chunk in enumerate(chunks):
                await self._send_chunk(
                    client,
                    channel_id=channel_id,
                    to_user_id=external_id,
                    text=chunk,
                    context_token=context_token,
                )
                if index < len(chunks) - 1 and CHUNK_DELAY_SECONDS:
                    await asyncio.sleep(CHUNK_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        except WeixinSessionExpired as exc:
            await self._set_delivery_pending(
                run_id, _error_text(exc), failed=False, cap_attempts=False
            )
            await self._mark_expired(channel_id, _error_text(exc))
            return
        except Exception as exc:
            await self._set_delivery_pending(run_id, _error_text(exc), failed=False)
            logger.warning("weixin delivery failed run=%s: %s", run_id, exc)
            return
        finally:
            if temporary:
                try:
                    await client.aclose()
                except Exception:
                    logger.exception("failed to close temporary weixin client")

        async with SessionLocal() as db:
            delivery = await db.get(ChannelDelivery, run_id)
            if delivery is not None:
                delivery.status = "delivered"
                delivery.last_error = None
                delivery.delivered_at = utcnow()
                await db.commit()

    async def _send_chunk(
        self,
        client: WeixinClient,
        *,
        channel_id: str,
        to_user_id: str,
        text: str,
        context_token: str | None,
    ) -> None:
        attempts = max(1, settings.channel_delivery_max_attempts)
        token = context_token
        dropped_token = False
        for attempt in range(1, attempts + 1):
            try:
                await client.send_text(
                    to_user_id=to_user_id,
                    text=text,
                    context_token=token,
                )
                return
            except WeixinSessionExpired:
                if token and not dropped_token:
                    # iLink accepts a tokenless send as a degraded retry.
                    dropped_token = True
                    token = None
                    continue
                raise
            except WeixinRateLimited as exc:
                if attempt >= attempts:
                    raise
                await asyncio.sleep(min(BACKOFF_DELAY_SECONDS, RETRY_DELAY_SECONDS * attempt))
                logger.warning(
                    "weixin send rate limited channel=%s attempt=%d: %s",
                    channel_id,
                    attempt,
                    exc,
                )
            except Exception as exc:
                if attempt >= attempts:
                    raise
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
                logger.warning(
                    "weixin send failed channel=%s attempt=%d/%d: %s",
                    channel_id,
                    attempt,
                    attempts,
                    exc,
                )
    async def sweep_deliveries(self, channel_id: str | None = None) -> None:
        async with SessionLocal() as db:
            statement = (
                select(ChannelDelivery)
                .where(ChannelDelivery.status.in_(("pending", "attempting")))
                .order_by(ChannelDelivery.created_at, ChannelDelivery.run_id)
            )
            if channel_id is not None:
                statement = statement.where(ChannelDelivery.channel_id == channel_id)
            else:
                active_ids = select(Channel.id).where(
                    Channel.type == "wechat", Channel.status == "active"
                )
                statement = statement.where(ChannelDelivery.channel_id.in_(active_ids))
            deliveries = list((await db.scalars(statement)).all())
        for delivery in deliveries:
            await self.deliver_run(
                delivery.run_id,
                recovered=delivery.status == "attempting",
            )

    # ------------------------------------------------------------------ QR login state

    def set_login(self, channel_id: str, *, qrcode: str, scan_url: str, base_url: str) -> None:
        self._login_sessions[channel_id] = {
            "qrcode": qrcode,
            "scan_url": scan_url,
            "base_url": base_url,
        }

    def get_login(self, channel_id: str) -> dict[str, str] | None:
        return self._login_sessions.get(channel_id)

    def clear_login(self, channel_id: str) -> None:
        self._login_sessions.pop(channel_id, None)

    def new_client(self, **kwargs: Any) -> WeixinClient:
        return self._client_factory(**kwargs)

    # ------------------------------------------------------------------ DB helpers

    def _build_client(self, credentials: ChannelCredentials) -> WeixinClient:
        return self._client_factory(
            account_id=credentials.account_id,
            token=credentials.token,
            base_url=credentials.base_url,
        )

    async def _load_credentials(self, channel_id: str) -> ChannelCredentials | None:
        async with SessionLocal() as db:
            channel = await db.get(Channel, channel_id)
            if (
                channel is None
                or channel.type != "wechat"
                or channel.status != "active"
                or not channel.bot_token
                or not channel.account_id
            ):
                return None
            return ChannelCredentials(
                channel_id=channel.id,
                account_id=channel.account_id,
                token=channel.bot_token,
                base_url=channel.base_url or "https://ilinkai.weixin.qq.com",
                sync_buf=channel.sync_buf or "",
            )

    async def _save_sync_buf(self, channel_id: str, sync_buf: str) -> None:
        async with SessionLocal() as db:
            channel = await db.get(Channel, channel_id)
            if channel is not None:
                channel.sync_buf = sync_buf
                await db.commit()

    async def _touch_last_seen(self, channel_id: str) -> None:
        async with SessionLocal() as db:
            channel = await db.get(Channel, channel_id)
            if channel is not None:
                channel.last_seen_at = utcnow()
                await db.commit()

    async def _mark_expired(self, channel_id: str, error: str) -> None:
        async with SessionLocal() as db:
            channel = await db.get(Channel, channel_id)
            if channel is not None:
                channel.status = "expired"
                channel.last_error = error
                await db.commit()
        if self._poll_tasks.get(channel_id) is not asyncio.current_task():
            await self.stop_channel(channel_id)

    async def _set_delivery_pending(
        self, run_id: str, error: str, *, failed: bool, cap_attempts: bool = True
    ) -> None:
        async with SessionLocal() as db:
            delivery = await db.get(ChannelDelivery, run_id)
            if delivery is None or delivery.status == "delivered":
                return
            delivery.last_error = error[:MAX_ERROR_TEXT]
            attempts = int(delivery.attempts or 0)
            if failed or (
                cap_attempts and attempts >= max(1, settings.channel_delivery_max_attempts)
            ):
                delivery.status = "failed"
            else:
                delivery.status = "pending"
            await db.commit()
