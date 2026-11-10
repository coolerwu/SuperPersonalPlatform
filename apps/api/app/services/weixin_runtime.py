"""In-process WeChat (iLink) channel runtime.

One poll task per active channel receives DMs. Configuration (channels,
conversations and their context tokens) lives in ``settings.json``; inbound
messages are handed to ``channel_intake`` which owns idempotency and turn
submission, and outbound replies go through the ``channel_delivery``
obligation rows. The SDK turn runner calls :meth:`deliver` right after a
channel turn reaches a terminal state; pending rows are also swept on startup
and when a channel becomes active again.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import settings
from app.services import channel_delivery, channel_intake, weixin_media
from app.services.attachments import SavedAttachment, build_attachment_header
from app.services.channel_text import split_reply
from app.services.commands import is_command_text, parse_command
from app.services.settings_store import ChannelSection
from app.services.weixin_client import (
    WeixinClient,
    WeixinMediaError,
    WeixinRateLimited,
    WeixinSessionExpired,
    is_group_message,
    is_self_message,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
RECOVERED_PREFIX = "♻️ 上一条回复可能已部分送达，以下为完整内容：\n\n"
MAX_CONSECUTIVE_FAILURES = 3
RETRY_DELAY_SECONDS = 2.0
BACKOFF_DELAY_SECONDS = 30.0
CHUNK_DELAY_SECONDS = 0.3
MAX_ERROR_TEXT = 500
CONTENT_DEDUP_SECONDS = 300.0
LAST_SEEN_INTERVAL_SECONDS = 60.0
MAX_FLUSH_RETRIES = 5
DELIVERY_SHUTDOWN_TIMEOUT_SECONDS = 10.0

# A channel that is inactive or whose login expired must not burn through the
# delivery retry budget: the obligation stays pending until the channel is
# usable again.
_UNBOUNDED_ATTEMPTS = 10**9


@dataclass(frozen=True)
class ChannelCredentials:
    channel_id: str
    account_id: str
    token: str
    base_url: str
    sync_buf: str
    agent_id: str = ""


@dataclass
class PendingBatch:
    texts: list[str]
    media: list[SavedAttachment]
    ids: list[str]
    sender_name: str
    context_token: str | None
    first_at: float
    last_at: float
    retries: int = 0


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_TEXT]


def _credentials_from(channel: ChannelSection) -> ChannelCredentials | None:
    if (
        channel.type != "wechat"
        or channel.status != "active"
        or not channel.bot_token
        or not channel.account_id
    ):
        return None
    return ChannelCredentials(
        channel_id=channel.id,
        account_id=channel.account_id,
        token=channel.bot_token,
        base_url=channel.base_url or DEFAULT_BASE_URL,
        sync_buf=channel.sync_buf or "",
        agent_id=channel.agent_id,
    )


class WeixinRuntime:
    def __init__(
        self,
        *,
        store: Any,
        registry: Any = None,
        client_factory: Callable[..., WeixinClient] | None = None,
        batch_seconds: float | None = None,
        batch_max_seconds: float | None = None,
    ) -> None:
        self.store = store
        # Assigned by ``main`` right after the registry exists; the runtime must
        # not be started before that.
        self.registry = registry
        self._client_factory = client_factory or WeixinClient
        self.batch_seconds = (
            settings.channel_inbound_batch_seconds
            if batch_seconds is None
            else max(0.0, batch_seconds)
        )
        self.batch_max_seconds = (
            settings.channel_inbound_batch_max_seconds
            if batch_max_seconds is None
            else max(0.0, batch_max_seconds)
        )
        self._poll_tasks: dict[str, asyncio.Task[Any]] = {}
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._clients: dict[str, WeixinClient] = {}
        self._login_sessions: dict[str, dict[str, str]] = {}
        self._delivering: set[int] = set()
        self._delivery_tasks: set[asyncio.Task[Any]] = set()
        self._recent_text: dict[str, float] = {}
        self._last_touch: dict[str, float] = {}
        self._pending: dict[tuple[str, str], PendingBatch] = {}
        self._pending_tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._inflight: set[tuple[str, str]] = set()
        self._pending_sync: dict[str, str] = {}
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._stop.clear()
        snapshot = await self.store.get()
        for channel in snapshot.channels:
            if channel.type == "wechat" and channel.status == "active":
                await self.start_channel(channel.id)
        await self.sweep_deliveries()

    async def stop(self) -> None:
        self._stop.set()
        pending_tasks = list(self._pending_tasks.values())
        self._pending_tasks.clear()
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        self._pending.clear()
        self._inflight.clear()
        self._pending_sync.clear()
        # Let in-flight sends finish first: a delivery may still be using a
        # cached poll client that the close below would yank away.
        delivery_tasks = list(self._delivery_tasks)
        if delivery_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*delivery_tasks, return_exceptions=True),
                    timeout=DELIVERY_SHUTDOWN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "weixin delivery shutdown timed out; cancelling %d task(s)",
                    len(delivery_tasks),
                )
                for task in delivery_tasks:
                    task.cancel()
                await asyncio.gather(*delivery_tasks, return_exceptions=True)
            self._delivery_tasks.clear()
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
        # Two concurrent activations (QR login + startup sweep) must not spawn
        # two poll loops for the same channel.
        lock = self._start_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
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
        # Serialize with start_channel: a pause/expiry must never race a QR
        # confirmation and leave a poll task running for a disabled channel.
        lock = self._start_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            self._drop_channel_buffers(channel_id)
            self._last_touch.pop(channel_id, None)
            task = self._poll_tasks.pop(channel_id, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            client = self._clients.pop(channel_id, None)
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    logger.exception(
                        "failed to close weixin client channel=%s", channel_id
                    )

    def running_channels(self) -> list[str]:
        return [channel_id for channel_id in self._poll_tasks]

    # ------------------------------------------------------------ inbound batching

    def _has_pending_for_channel(self, channel_id: str) -> bool:
        return any(key[0] == channel_id for key in self._pending) or any(
            key[0] == channel_id for key in self._inflight
        )

    def _drop_pending(self, key: tuple[str, str]) -> None:
        task = self._pending_tasks.pop(key, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._pending.pop(key, None)

    def _drop_channel_buffers(self, channel_id: str) -> None:
        for key in [item for item in self._pending if item[0] == channel_id]:
            self._drop_pending(key)
        self._pending_sync.pop(channel_id, None)

    def _schedule_flush(
        self, key: tuple[str, str], delay: float | None = None
    ) -> None:
        previous = self._pending_tasks.pop(key, None)
        if previous is not None and previous is not asyncio.current_task():
            previous.cancel()
        batch = self._pending.get(key)
        if batch is None:
            return
        if delay is None:
            now = time.monotonic()
            delay = min(
                self.batch_seconds - (now - batch.last_at),
                self.batch_max_seconds - (now - batch.first_at),
            )
        task = asyncio.create_task(
            self._flush_after(key, max(0.0, delay)),
            name=f"weixin-flush-{key[0]}-{key[1][:8]}",
        )
        self._pending_tasks[key] = task

    async def _flush_after(self, key: tuple[str, str], delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.flush_pending(key)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("weixin batch flush failed channel=%s", key[0])
            if key in self._pending:
                self._schedule_flush(key, RETRY_DELAY_SECONDS)

    def _enqueue(
        self,
        credentials: ChannelCredentials,
        *,
        sender_id: str,
        message_id: str,
        text: str,
        media: list[SavedAttachment],
        context_token: str | None,
        sender_name: str,
    ) -> None:
        key = (credentials.channel_id, sender_id)
        now = time.monotonic()
        batch = self._pending.get(key)
        if batch is None:
            batch = PendingBatch(
                texts=[],
                media=[],
                ids=[],
                sender_name=sender_name,
                context_token=context_token,
                first_at=now,
                last_at=now,
            )
            self._pending[key] = batch
        if text.strip():
            batch.texts.append(text.strip())
        batch.media.extend(media)
        if message_id not in batch.ids:
            batch.ids.append(message_id)
        batch.sender_name = sender_name or batch.sender_name
        batch.context_token = context_token or batch.context_token
        batch.last_at = now
        size = sum(len(item) for item in batch.texts) + len(
            build_attachment_header(batch.media)
        )
        if self.batch_seconds <= 0 or size > settings.channel_inbound_max_chars:
            self._schedule_flush(key, 0)
        else:
            self._schedule_flush(key)

    def _batch_content(self, batch: PendingBatch) -> str:
        header = build_attachment_header(batch.media)
        body = "\n\n".join(text for text in batch.texts if text.strip())
        content = f"{header}\n\n{body}" if header and body else header or body
        limit = settings.channel_inbound_max_chars
        if len(content) > limit:
            note = "\n\n（内容过长，已截断）"
            room = max(0, limit - len(header) - len(note) - 2)
            body = f"{body[:room]}{note}"
            content = f"{header}\n\n{body}" if header else body
            if len(content) > limit:
                content = content[:limit]
        return content

    async def flush_pending(self, key: tuple[str, str]) -> None:
        """Turn one buffered conversation packet into exactly one queued turn."""
        task = self._pending_tasks.pop(key, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        batch = self._pending.pop(key, None)
        if batch is None or not batch.ids:
            return
        channel_id, sender_id = key
        # Mark the channel busy before the first await so the sync cursor is
        # never persisted while this batch is still being processed.
        self._inflight.add(key)
        save_sync = True
        try:
            content = self._batch_content(batch)
            if not content.strip():
                return
            try:
                # Defensive re-check: a batch claim is atomic, so an id already
                # claimed elsewhere must not drag its new neighbours into a
                # duplicate drop.
                seen = await channel_intake.inbound_seen(
                    channel_id, batch.ids
                )
                if seen:
                    batch.ids = [
                        item for item in batch.ids if item not in seen
                    ]
                    if not batch.ids:
                        return
                result = await channel_intake.ingest(
                    store=self.store,
                    registry=self.registry,
                    channel_id=channel_id,
                    external_message_ids=batch.ids,
                    external_id=sender_id,
                    display_name=batch.sender_name,
                    text=content,
                    context_token=batch.context_token,
                )
            except ValueError as exc:
                # Permanent rejection (channel/agent unavailable, bad
                # arguments): drop the packet and keep the last persisted
                # cursor, so a restart replays it instead of retrying forever.
                save_sync = False
                self._pending_sync.pop(channel_id, None)
                logger.warning(
                    "weixin batch rejected channel=%s sender=%s: %s",
                    channel_id,
                    sender_id[:8],
                    exc,
                )
                return
            except Exception as exc:
                save_sync = False
                self._retry_or_drop_batch(key, batch, exc)
                return

            # A duplicate batch was already claimed (and its turn queued) by
            # an earlier delivery; advancing the cursor is all that is left.
            if not result.duplicate and result.reply:
                credentials = await self._load_credentials(channel_id)
                if credentials is not None:
                    await self._send_text(
                        credentials,
                        to_user_id=sender_id,
                        text=result.reply,
                        context_token=batch.context_token,
                        client=self._clients.get(channel_id),
                        label="batch",
                    )
        except asyncio.CancelledError:
            # Shutdown or a reschedule interrupted the ingest: keep the saved
            # cursor where it was so iLink replays the packet.
            save_sync = False
            raise
        finally:
            self._inflight.discard(key)
            if save_sync:
                await self._save_sync_if_idle(channel_id)

    def _retry_or_drop_batch(
        self,
        key: tuple[str, str],
        batch: PendingBatch,
        exc: BaseException,
    ) -> None:
        channel_id = key[0]
        batch.retries += 1
        if batch.retries > MAX_FLUSH_RETRIES:
            # Transient failures get a bounded number of retries; after that
            # the packet is dropped and the old cursor is left for a replay.
            self._pending_sync.pop(channel_id, None)
            logger.warning(
                "weixin batch dropped after %d attempts channel=%s: %s",
                batch.retries,
                channel_id,
                exc,
                exc_info=True,
            )
            return
        logger.warning(
            "weixin batch ingest failed channel=%s attempt=%d/%d: %s",
            channel_id,
            batch.retries,
            MAX_FLUSH_RETRIES,
            exc,
        )
        current = self._pending.get(key)
        if current is None:
            self._pending[key] = batch
        else:
            current.texts[:0] = batch.texts
            current.media[:0] = batch.media
            current.ids[:0] = [
                item for item in batch.ids if item not in current.ids
            ]
            current.first_at = min(current.first_at, batch.first_at)
            current.retries = batch.retries
        self._schedule_flush(key, RETRY_DELAY_SECONDS)

    async def flush_all(self) -> None:
        # TODO(decommission): test compatibility only; the runtime has no
        # production caller for a synchronous flush of every pending batch.
        for key in list(self._pending):
            await self.flush_pending(key)

    async def _save_sync_if_idle(self, channel_id: str) -> None:
        """Persist the packet cursor once no batch is in flight.

        Keeping the old cursor after a permanent ingest error is best effort:
        if the channel stays active and another packet arrives, the poll loop
        can still advance it, so the dropped packet is not guaranteed to be
        replayed after a restart.
        """
        if self._has_pending_for_channel(channel_id):
            return
        sync_buf = self._pending_sync.pop(channel_id, None)
        if sync_buf:
            await self._save_sync_buf(channel_id, sync_buf)

    async def _send_text(
        self,
        credentials: ChannelCredentials,
        *,
        to_user_id: str,
        text: str,
        context_token: str | None,
        client: WeixinClient | None,
        label: str,
    ) -> None:
        if not to_user_id:
            return
        temporary = client is None
        if client is None:
            client = self._build_client(credentials)
        try:
            await self._send_chunk(
                client,
                channel_id=credentials.channel_id,
                to_user_id=to_user_id,
                text=text,
                context_token=context_token,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "weixin %s reply failed channel=%s: %s",
                label,
                credentials.channel_id,
                exc,
            )
        finally:
            if temporary:
                try:
                    await client.aclose()
                except Exception:
                    logger.exception("failed to close temporary weixin client")

    async def _send_unsupported(
        self,
        credentials: ChannelCredentials,
        message: dict[str, Any],
        kinds: list[str],
        client: WeixinClient | None,
    ) -> None:
        to_user_id = str(message.get("from_user_id") or "").strip()
        context_token = str(message.get("context_token") or "").strip() or None
        await self._send_text(
            credentials,
            to_user_id=to_user_id,
            text=weixin_media.unsupported_text(kinds),
            context_token=context_token,
            client=client,
            label="unsupported",
        )

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
                            await self._handle_message(credentials, message, client)
                        except Exception:
                            logger.exception(
                                "weixin inbound failed channel=%s", channel_id
                            )
                new_buf = str(data.get("get_updates_buf") or "")
                if new_buf and new_buf != sync_buf:
                    sync_buf = new_buf
                    if self._has_pending_for_channel(channel_id):
                        # Keep the old cursor until the whole packet is in the
                        # database; a restart in the window replays it.
                        self._pending_sync[channel_id] = sync_buf
                    else:
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

    async def _handle_message(
        self,
        credentials: ChannelCredentials,
        message: Any,
        client: WeixinClient | None = None,
    ) -> None:
        if not isinstance(message, dict):
            return
        sender_id = str(message.get("from_user_id") or "").strip()
        if not sender_id or is_self_message(message, credentials.account_id):
            return
        if is_group_message(message):
            # v1 is DM-only; iLink bot identities usually do not receive group
            # events anyway.
            return

        parsed = weixin_media.parse_inbound(message.get("item_list"))
        text = parsed.text.strip()
        is_command = is_command_text(text)
        if not is_command and not parsed.has_content and not parsed.unsupported:
            return

        digest = hashlib.sha256((text or f"media:{sender_id}").encode("utf-8")).hexdigest()[:16]
        dedup_key = f"{credentials.channel_id}:{sender_id}:{digest}"
        if not is_command and text and self._text_seen(dedup_key):
            logger.debug(
                "weixin content dedup channel=%s sender=%s",
                credentials.channel_id,
                sender_id[:8],
            )
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
        context_token = str(message.get("context_token") or "").strip() or None
        key = (credentials.channel_id, sender_id)

        if is_command:
            command = parse_command(text)
            if command in ("/new", "/clear"):
                # Start the new session with nothing buffered from before.
                await self.flush_pending(key)
            elif command == "/stop":
                # A stop means the buffered follow-up should not run at all.
                self._drop_pending(key)
            # /help, /status and unknown commands must not disturb the batch
            # window or flush partial text into a new turn.
            # A failed command ingest is logged and dropped; the user has to
            # resend the command (there is no turn to retry).
            try:
                result = await channel_intake.ingest(
                    store=self.store,
                    registry=self.registry,
                    channel_id=credentials.channel_id,
                    external_message_ids=[message_id],
                    external_id=sender_id,
                    display_name=sender_name or sender_id,
                    text=text,
                    context_token=context_token,
                )
            except ValueError as exc:
                logger.warning(
                    "weixin command rejected channel=%s: %s",
                    credentials.channel_id,
                    exc,
                )
                return
            except Exception:
                logger.exception(
                    "weixin command failed channel=%s", credentials.channel_id
                )
                return
            if not result.duplicate and result.reply:
                await self._send_text(
                    credentials,
                    to_user_id=sender_id,
                    text=result.reply,
                    context_token=context_token,
                    client=client,
                    label="command",
                )
            await self._save_sync_if_idle(credentials.channel_id)
            return

        # Drop ids already claimed by a previous delivery before they enter a
        # new batch: a batch claim is atomic, so one stale id would otherwise
        # swallow the new messages bundled with it.
        pending = self._pending.get(key)
        candidate_ids = list(pending.ids) if pending is not None else []
        if message_id not in candidate_ids:
            candidate_ids.append(message_id)
        seen = await channel_intake.inbound_seen(
            credentials.channel_id, candidate_ids
        )
        if pending is not None and seen:
            pending.ids = [item for item in pending.ids if item not in seen]
        if message_id in seen:
            return

        if any(message_id in batch.ids for batch in self._pending.values()):
            return

        if not parsed.has_content:
            # Unsupported-only messages create no turn, so claim their ids here
            # to keep an iLink replay after a restart from replying twice.
            if not await channel_intake.claim_inbound(
                credentials.channel_id,
                [message_id],
                external_id=sender_id,
            ):
                return
            await self._send_unsupported(
                credentials, message, parsed.unsupported, client
            )
            return

        notes: list[str] = []
        saved: list[SavedAttachment] = []
        if parsed.media:
            agent_id = credentials.agent_id or "default"
            temporary = client is None
            media_client = client if client is not None else self._build_client(credentials)
            try:
                for media_ref in parsed.media[: settings.channel_media_max_items]:
                    try:
                        saved.append(
                            await weixin_media.download_and_save(
                                media_client,
                                media_ref,
                                agent_id=agent_id,
                                message_id=message_id,
                                max_bytes=settings.channel_media_max_bytes,
                            )
                        )
                    except (WeixinMediaError, OSError, ValueError) as exc:
                        label = "图片" if media_ref.kind == "image" else "文件"
                        notes.append(f"（{label}下载失败：{exc}）")
                        logger.warning(
                            "weixin media skipped channel=%s message=%s: %s",
                            credentials.channel_id,
                            message_id[:16],
                            exc,
                        )
                if len(parsed.media) > settings.channel_media_max_items:
                    notes.append(
                        f"（超过 {settings.channel_media_max_items} 个附件，其余未下载）"
                    )
            finally:
                if temporary:
                    try:
                        await media_client.aclose()
                    except Exception:
                        logger.exception("failed to close temporary weixin client")

        if parsed.unsupported:
            labels = "、".join(dict.fromkeys(parsed.unsupported))
            notes.append(f"（已忽略一条{labels}消息）")
        quote = await self._render_quote(
            credentials.channel_id, sender_id, parsed, message_id
        )
        parts = [quote, text, *notes]
        content = "\n\n".join(part.strip() for part in parts if part.strip())
        if not content and not saved:
            return
        self._enqueue(
            credentials,
            sender_id=sender_id,
            message_id=message_id,
            text=content,
            media=saved,
            context_token=context_token,
            sender_name=sender_name or sender_id,
        )
        if text:
            self._remember_text(dedup_key)

    async def _render_quote(
        self,
        channel_id: str,
        external_id: str,
        parsed: weixin_media.ParsedInbound,
        message_id: str,
    ) -> str:
        """Render ref_msg quotes, recovering text iLink left out of the payload."""
        rendered: list[str] = []
        for quote in parsed.quotes:
            resolved = ""
            if quote.unresolved:
                try:
                    resolved = (
                        await channel_intake.resolve_quoted_text(
                            channel_id,
                            external_id,
                            quote.msg_id or None,
                            quote.create_time_ms,
                        )
                        or ""
                    )
                except Exception:
                    logger.exception(
                        "weixin quote lookup failed channel=%s", channel_id
                    )
                if not resolved:
                    logger.info(
                        "weixin quote unresolved channel=%s message=%s msg_id=%s create_time_ms=%s",
                        channel_id,
                        message_id[:16],
                        quote.msg_id or "-",
                        quote.create_time_ms or "-",
                    )
            rendered.append(quote.render(resolved))
        return "\n\n".join(rendered).strip()

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

    def schedule_delivery(
        self, delivery_id: int, *, recovered: bool = False
    ) -> None:
        """Start a send in the background so turn teardown never waits on it."""
        task = asyncio.create_task(
            self.deliver(delivery_id, recovered=recovered),
            name=f"weixin-delivery-{delivery_id}",
        )
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_done)

    def _delivery_done(self, task: asyncio.Task[Any]) -> None:
        self._delivery_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "weixin background delivery failed: %s: %s",
                type(exc).__name__,
                exc,
            )

    async def deliver(self, delivery_id: int, *, recovered: bool = False) -> None:
        """Send one filled delivery obligation, if it is still owed."""
        if delivery_id in self._delivering:
            # Another in-process send already owns this row.
            return
        self._delivering.add(delivery_id)
        try:
            await self._deliver_locked(delivery_id, recovered=recovered)
        finally:
            self._delivering.discard(delivery_id)

    async def _deliver_locked(self, delivery_id: int, *, recovered: bool) -> None:
        row = await channel_delivery.get(delivery_id)
        if row is None or row.status in ("sent", "failed"):
            return
        content = row.content or ""
        if not content.strip():
            # Empty rows belong to turns that have not finished yet.
            return
        if row.status == "attempting" and not recovered:
            return
        if not await channel_delivery.mark_attempting(delivery_id):
            return

        snapshot = await self.store.get()
        channel = next(
            (item for item in snapshot.channels if item.id == row.channel_id), None
        )
        conversation = snapshot.channel_conversations.get(
            f"{row.channel_id}:{row.external_id}"
        )
        channel_ok = (
            channel is not None
            and channel.type == "wechat"
            and channel.status == "active"
        )
        if not channel_ok or conversation is None:
            reason = (
                "conversation is missing" if channel_ok else "channel is not active"
            )
            await channel_delivery.mark_pending(
                delivery_id,
                reason,
                max_attempts=settings.channel_delivery_max_attempts,
            )
            return

        credentials = _credentials_from(channel)
        client = self._clients.get(channel.id)
        temporary = False
        if credentials is None:
            await channel_delivery.mark_pending(
                delivery_id,
                "channel credentials are missing",
                max_attempts=settings.channel_delivery_max_attempts,
            )
            return
        if client is None:
            client = self._build_client(credentials)
            temporary = True

        try:
            chunks = split_reply(content)
            if recovered and chunks:
                chunks[0] = f"{RECOVERED_PREFIX}{chunks[0]}"
            for index, chunk in enumerate(chunks):
                await self._send_chunk(
                    client,
                    channel_id=channel.id,
                    to_user_id=row.external_id,
                    text=chunk,
                    context_token=conversation.context_token,
                )
                if index < len(chunks) - 1 and CHUNK_DELAY_SECONDS:
                    await asyncio.sleep(CHUNK_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        except WeixinSessionExpired as exc:
            # Keep the obligation for after the next login; it must not fail
            # just because the stored token expired.
            await channel_delivery.mark_pending(
                delivery_id,
                _error_text(exc),
                max_attempts=_UNBOUNDED_ATTEMPTS,
            )
            await self._mark_expired(channel.id, _error_text(exc))
            return
        except Exception as exc:
            await channel_delivery.mark_pending(
                delivery_id,
                _error_text(exc),
                max_attempts=settings.channel_delivery_max_attempts,
            )
            logger.warning(
                "weixin delivery failed delivery=%s: %s", delivery_id, exc
            )
            return
        finally:
            if temporary:
                try:
                    await client.aclose()
                except Exception:
                    logger.exception("failed to close temporary weixin client")

        await channel_delivery.mark_sent(delivery_id)

    async def sweep_deliveries(self, channel_id: str | None = None) -> None:
        snapshot = await self.store.get()
        active = {
            channel.id
            for channel in snapshot.channels
            if channel.type == "wechat" and channel.status == "active"
        }
        if channel_id is not None:
            active &= {channel_id}
        for row in await channel_delivery.list_pending():
            if row.channel_id not in active:
                continue
            # Startup/channel activation must not block on the network; the
            # attempting flag decides whether the recovered prefix is used.
            self.schedule_delivery(
                row.id,
                recovered=row.status == "attempting",
            )

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

    # ------------------------------------------------------------------ state helpers

    def _build_client(self, credentials: ChannelCredentials) -> WeixinClient:
        return self._client_factory(
            account_id=credentials.account_id,
            token=credentials.token,
            base_url=credentials.base_url,
        )

    async def _load_credentials(self, channel_id: str) -> ChannelCredentials | None:
        snapshot = await self.store.get()
        channel = next(
            (item for item in snapshot.channels if item.id == channel_id), None
        )
        if channel is None:
            return None
        return _credentials_from(channel)

    async def _save_sync_buf(self, channel_id: str, sync_buf: str) -> None:
        await self.store.update_channel_fields(channel_id, sync_buf=sync_buf)

    async def _touch_last_seen(self, channel_id: str) -> None:
        now = time.monotonic()
        previous = self._last_touch.get(channel_id)
        if previous is not None and now - previous < LAST_SEEN_INTERVAL_SECONDS:
            return
        self._last_touch[channel_id] = now
        stamp = datetime.now(timezone.utc).isoformat()
        await self.store.update_channel_fields(channel_id, last_seen_at=stamp)

    async def _mark_expired(self, channel_id: str, error: str) -> None:
        # A login expiry invalidates the buffered packets too: retrying them
        # would only spam tracebacks until the channel logs in again.
        self._drop_channel_buffers(channel_id)
        self._last_touch.pop(channel_id, None)
        await self.store.update_channel_fields(
            channel_id, status="expired", last_error=error
        )
        if self._poll_tasks.get(channel_id) is not asyncio.current_task():
            await self.stop_channel(channel_id)
