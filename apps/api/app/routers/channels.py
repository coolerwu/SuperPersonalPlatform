"""Channel management and WeChat QR login endpoints (single-user cookie auth)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import Agent, Channel, ChannelConversation, utcnow
from app.schemas import (
    ChannelCreate,
    ChannelLoginOut,
    ChannelLoginStatusOut,
    ChannelOut,
    ChannelUpdate,
)
from app.services import channel_service
from app.services.weixin_client import (
    ILINK_BASE_URL,
    WeixinError,
    qr_svg,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"], dependencies=[Depends(require_auth)])

QR_LOGIN_SECONDS = 480


def _runtime(request: Request):
    return getattr(request.app.state, "weixin", None)


async def _conversation_count(db: AsyncSession, channel_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count(ChannelConversation.id)).where(
                ChannelConversation.channel_id == channel_id
            )
        )
        or 0
    )


async def _channel_out(db: AsyncSession, channel: Channel) -> ChannelOut:
    return ChannelOut(
        id=channel.id,
        agent_id=channel.agent_id,
        type=channel.type,
        name=channel.name,
        status=channel.status,
        account_id=channel.account_id,
        has_credentials=bool(channel.account_id and channel.bot_token),
        conversation_count=await _conversation_count(db, channel.id),
        last_seen_at=channel.last_seen_at,
        last_error=channel.last_error,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


async def _load_channel(db: AsyncSession, channel_id: str) -> Channel:
    channel = await db.get(Channel, channel_id)
    if channel is None or channel.type != "wechat":
        raise HTTPException(status_code=404, detail="channel not found")
    return channel


@router.get("", response_model=list[ChannelOut])
async def list_channels(
    agent_id: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> list[ChannelOut]:
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")
    channels = (
        await db.scalars(
            select(Channel)
            .where(Channel.agent_id == agent_id)
            .order_by(Channel.created_at)
        )
    ).all()
    return [await _channel_out(db, channel) for channel in channels]


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(payload: ChannelCreate, db: AsyncSession = Depends(get_db)) -> ChannelOut:
    agent = await db.get(Agent, payload.agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")
    channel = Channel(
        agent_id=agent.id,
        type="wechat",
        name=payload.name.strip(),
        status="pairing",
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return await _channel_out(db, channel)


@router.put("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: str,
    payload: ChannelUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChannelOut:
    channel = await _load_channel(db, channel_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        channel.name = str(data["name"]).strip()
    if "status" in data and data["status"] is not None:
        next_status = str(data["status"])
        if next_status == "active" and not (channel.account_id and channel.bot_token):
            raise HTTPException(status_code=400, detail="channel has not completed WeChat login")
        channel.status = next_status
        await db.commit()
        runtime = _runtime(request)
        if runtime is not None:
            if next_status == "active":
                await runtime.start_channel(channel.id)
                await runtime.sweep_deliveries(channel.id)
            else:
                await runtime.stop_channel(channel.id)
    else:
        await db.commit()
    await db.refresh(channel)
    return await _channel_out(db, channel)


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    channel = await _load_channel(db, channel_id)
    runtime = _runtime(request)
    if runtime is not None:
        await runtime.stop_channel(channel.id)
        runtime.clear_login(channel.id)
    await channel_service.delete_channels(db, [channel.id])
    await db.commit()
    return {"deleted": True}


@router.post("/{channel_id}/wechat/login", response_model=ChannelLoginOut)
async def start_wechat_login(
    channel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChannelLoginOut:
    channel = await _load_channel(db, channel_id)
    if channel.status == "active":
        raise HTTPException(status_code=409, detail="channel is already connected; use relogin")
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="WeChat runtime is not available")
    client = runtime.new_client()
    try:
        data = await client.fetch_qr()
    except WeixinError as exc:
        logger.warning("weixin QR request failed channel=%s: %s", channel_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("weixin QR request failed channel=%s", channel_id)
        raise HTTPException(status_code=502, detail="failed to request WeChat QR code") from exc
    finally:
        await client.aclose()

    qrcode = str(data.get("qrcode") or "").strip()
    scan_url = str(data.get("qrcode_img_content") or "").strip() or qrcode
    if not qrcode or not scan_url:
        raise HTTPException(status_code=502, detail="WeChat QR response was incomplete")

    base_url = ILINK_BASE_URL
    runtime.set_login(channel.id, qrcode=qrcode, scan_url=scan_url, base_url=base_url)
    channel.status = "pairing"
    channel.last_error = None
    await db.commit()
    return ChannelLoginOut(
        qrcode=qrcode,
        scan_url=scan_url,
        qr_svg=qr_svg(scan_url),
        expires_in=QR_LOGIN_SECONDS,
    )


@router.get("/{channel_id}/wechat/login/status", response_model=ChannelLoginStatusOut)
async def wechat_login_status(
    channel_id: str,
    request: Request,
    qrcode: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> ChannelLoginStatusOut:
    channel = await _load_channel(db, channel_id)
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="WeChat runtime is not available")

    login = runtime.get_login(channel.id)
    if login is not None and login.get("qrcode") and login["qrcode"] != qrcode:
        raise HTTPException(status_code=409, detail="QR code does not match the active login")
    base_url = (login or {}).get("base_url") or channel.base_url or ILINK_BASE_URL

    client = runtime.new_client()
    try:
        data = await client.qr_status(qrcode, base_url=base_url)
    except WeixinError as exc:
        logger.warning("weixin QR status failed channel=%s: %s", channel_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("weixin QR status failed channel=%s", channel_id)
        raise HTTPException(status_code=502, detail="failed to query WeChat QR status") from exc
    finally:
        await client.aclose()

    status = str(data.get("status") or "wait")
    if status == "scaned_but_redirect" and data.get("redirect_host"):
        redirect_host = str(data["redirect_host"]).strip()
        if redirect_host:
            base_url = f"https://{redirect_host}"
            runtime.set_login(
                channel.id,
                qrcode=qrcode,
                scan_url=(login or {}).get("scan_url") or qrcode,
                base_url=base_url,
            )
        return ChannelLoginStatusOut(status=status)

    if status != "confirmed":
        return ChannelLoginStatusOut(status=status)

    account_id = str(data.get("ilink_bot_id") or "").strip()
    bot_token = str(data.get("bot_token") or "").strip()
    if not account_id or not bot_token:
        raise HTTPException(status_code=502, detail="WeChat login response was incomplete")
    channel.account_id = account_id
    channel.bot_token = bot_token
    channel.base_url = str(data.get("baseurl") or base_url).rstrip("/")
    channel.ilink_user_id = str(data.get("ilink_user_id") or "")
    channel.status = "active"
    channel.last_error = None
    channel.sync_buf = ""
    await db.commit()
    runtime.clear_login(channel.id)
    await runtime.start_channel(channel.id)
    await runtime.sweep_deliveries(channel.id)
    return ChannelLoginStatusOut(status="confirmed", account_id=account_id)


@router.post("/{channel_id}/wechat/relogin", response_model=ChannelOut)
async def relogin_wechat(
    channel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChannelOut:
    channel = await _load_channel(db, channel_id)
    runtime = _runtime(request)
    if runtime is not None:
        await runtime.stop_channel(channel.id)
        runtime.clear_login(channel.id)
    channel.status = "pairing"
    channel.bot_token = ""
    channel.account_id = ""
    channel.ilink_user_id = ""
    channel.sync_buf = ""
    channel.last_error = None
    await db.commit()
    await db.refresh(channel)
    return await _channel_out(db, channel)
