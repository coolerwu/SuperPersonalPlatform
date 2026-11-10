"""Push browser screenshots to the WeChat conversation that started a turn.

A channel turn's final text is delivered when the turn ends, but a login QR
must reach the user while the browser is still open. This module uploads a
screenshot to the iLink CDN and sends it as an image message as soon as the
screenshot is taken.

Channel configuration lives in ``settings.json`` now. The tool layer that
produces screenshots is not wired into the SDK runtime yet, so nothing calls
``send_session_image`` in this version; it is kept ready for that migration.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.services.settings_store import ChannelSection
from app.services.weixin_client import WeixinClient, WeixinRateLimited

logger = logging.getLogger(__name__)

SEND_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5


def _build_client(channel: ChannelSection) -> WeixinClient:
    return WeixinClient(
        account_id=channel.account_id,
        token=channel.bot_token,
        base_url=channel.base_url or "",
    )


def _conversation_key_parts(key: str) -> tuple[str, str] | None:
    """Split a ``channel_id:external_id`` settings key."""
    channel_id, separator, external_id = key.partition(":")
    if not separator or not channel_id or not external_id:
        return None
    return channel_id, external_id


async def send_session_image(
    session_id: str, image_path: Path, *, store=None
) -> bool:
    """Send one image to the channel bound to ``session_id``.

    Returns False when the session has no active WeChat binding or the image
    cannot be read. Send failures are logged and also return False; they never
    fail the turn itself.
    """
    if not session_id:
        return False
    if store is None:
        from app.services.settings_store import get_settings_store

        store = get_settings_store()
    current = await store.get()
    binding = next(
        (
            (key, conversation)
            for key, conversation in current.channel_conversations.items()
            if conversation.session_id == session_id
        ),
        None,
    )
    if binding is None:
        return False
    key, conversation = binding
    parts = _conversation_key_parts(key)
    channel = next(
        (item for item in current.channels if item.id == conversation.channel_id),
        None,
    )
    if (
        parts is None
        or channel is None
        or channel.type != "wechat"
        or channel.status != "active"
        or not channel.bot_token
        or not channel.account_id
    ):
        return False
    to_user_id = parts[1]
    context_token = conversation.context_token
    client = _build_client(channel)

    try:
        try:
            data = image_path.read_bytes()
        except OSError:
            logger.warning("channel image unreadable: %s", image_path, exc_info=True)
            return False
        if not data or len(data) > settings.browser_screenshot_max_bytes:
            logger.warning("channel image skipped session=%s bytes=%s", session_id, len(data))
            return False
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                await client.send_image(
                    to_user_id=to_user_id,
                    image=data,
                    context_token=context_token,
                )
                return True
            except WeixinRateLimited as exc:
                if attempt >= SEND_ATTEMPTS:
                    raise
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
                logger.warning(
                    "weixin image rate limited session=%s attempt=%d: %s",
                    session_id,
                    attempt,
                    exc,
                )
            except Exception:
                if attempt >= SEND_ATTEMPTS:
                    raise
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
                logger.warning(
                    "weixin image send failed session=%s attempt=%d",
                    session_id,
                    attempt,
                    exc_info=True,
                )
        return False
    except Exception:
        logger.warning("weixin image send failed session=%s", session_id, exc_info=True)
        return False
    finally:
        await client.aclose()
