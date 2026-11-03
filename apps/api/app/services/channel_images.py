"""Push browser screenshots to the WeChat conversation that started a Run.

A channel Run's final text is delivered when the Run terminates, but a login
QR must reach the user while the browser is still open. This module uploads a
screenshot to the iLink CDN and sends it as an image message as soon as the
screenshot is taken.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.models import Channel, ChannelConversation, ChannelRunLink
from app.services.weixin_client import WeixinClient, WeixinRateLimited

logger = logging.getLogger(__name__)

SEND_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5


def _build_client(channel: Channel) -> WeixinClient:
    return WeixinClient(
        account_id=channel.account_id,
        token=channel.bot_token,
        base_url=channel.base_url or "",
    )


async def send_run_image(run_id: str, image_path: Path) -> bool:
    """Send one image to the channel bound to `run_id`.

    Returns False when the Run has no active WeChat link or the image cannot be
    read. Send failures are logged and also return False; they never fail the
    Run itself.
    """
    if not run_id:
        return False
    async with SessionLocal() as db:
        link = await db.get(ChannelRunLink, run_id)
        if link is None:
            return False
        channel = await db.get(Channel, link.channel_id)
        conversation = await db.get(ChannelConversation, link.conversation_id)
        if (
            channel is None
            or conversation is None
            or channel.type != "wechat"
            or channel.status != "active"
            or not channel.bot_token
            or not channel.account_id
        ):
            return False
        to_user_id = conversation.external_id
        context_token = conversation.context_token
        client = _build_client(channel)

    try:
        try:
            data = image_path.read_bytes()
        except OSError:
            logger.warning("channel image unreadable: %s", image_path, exc_info=True)
            return False
        if not data or len(data) > settings.browser_screenshot_max_bytes:
            logger.warning("channel image skipped run=%s bytes=%s", run_id, len(data))
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
                    "weixin image rate limited run=%s attempt=%d: %s",
                    run_id,
                    attempt,
                    exc,
                )
            except Exception:
                if attempt >= SEND_ATTEMPTS:
                    raise
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
                logger.warning(
                    "weixin image send failed run=%s attempt=%d",
                    run_id,
                    attempt,
                    exc_info=True,
                )
        return False
    except Exception:
        logger.warning("weixin image send failed run=%s", run_id, exc_info=True)
        return False
    finally:
        await client.aclose()
